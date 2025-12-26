import base64
import mimetypes
import os
import re
import subprocess
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import google.generativeai as genai
import markdown
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from google.cloud import texttospeech
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- 設定 ---
TTS_VOICE_NAME = "ja-JP-Standard-B"
TEMP_MP3_FILE = "temp_summary_audio.mp3"
TTS_SPEAKING_RATE = 1.8
# -----------------

app = Flask(__name__)
app.secret_key = "your_secret_key"

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_TO = os.getenv("GMAIL_TO")
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

genai.configure(api_key=GEMINI_API_KEY)
CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)


def clean_youtube_url(url: str) -> str:
    parsed = urlparse(url)

    if "youtu.be" in parsed.netloc:
        return f"https://www.youtube.com/watch?v={parsed.path.strip('/')}"

    if "youtube.com" in parsed.netloc and "watch" in parsed.path:
        qs = parse_qs(parsed.query)
        video_id = qs.get("v", [None])[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

    return url


def download_captions(youtube_url: str) -> Path:
    clean_url = clean_youtube_url(youtube_url)
    subprocess.run(
        [
            "yt-dlp",
            "--write-auto-sub",
            "--sub-lang",
            "ja",
            "--skip-download",
            "--output",
            str(CAPTIONS_DIR / "%(title)s [%(id)s].%(ext)s"),
            clean_url,
        ],
        check=True,
    )
    return next(CAPTIONS_DIR.glob("*.vtt"), None)


def parse_vtt(vtt_path: Path) -> List[str]:
    with vtt_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    text_lines: List[str] = []
    skip_next = False
    for line in lines:
        line = line.strip()
        if re.match(r"^\d\d:\d\d:\d\d\.\d\d\d -->", line):
            skip_next = False
            continue
        elif line == "" or line.startswith("WEBVTT") or re.match(r"^\d+$", line):
            continue
        elif not skip_next:
            line = re.sub(r"<.*?>", "", line)
            text_lines.append(line)
            skip_next = True

    return text_lines


def clean_text(text_lines: List[str]) -> str:
    seen, cleaned = set(), []
    for line in text_lines:
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            cleaned.append(line)
    return "\n".join(cleaned)


def create_prompt(cleaned_text: str, video_title: str, video_url: str) -> str:
    return f"""以下はYouTube 動画「{video_title}」の日本語字幕全文です。この内容をもとに…

【ロール】
あなたは「テクニカル講師兼リサーチライター」です。文字起こし"のみ"を根拠に、初心者〜中級者が再現できる形で要約します。主観と事実を分け、不明点は「文字起こしからは不明」と書いてください。

【出力ルール】
- 日本語／簡潔。結論→理由→補足の順。
- 数値・期間・時間軸・パラメータは動画に出たものだけ（創作禁止）。
- シグナルは「条件」＋「確定条件（終値/出来高/足確定など）」で表現。

【入力メタ】
- タイトル:{video_title} / URL:{video_url}

【出力フォーマット】

① 一言要約（2–4行）
この動画は何を学べるか（マット氏のスイング手法／3つの失敗／3つのルール／時間軸と期間）。

② 手法の全体像（事実）
- 時間軸・想定保有期間：
- 使う判断材料（例：トレンド/サポレジ/出来高 など）：
- 目的（エントリー/損切/利確の役割分担）：

③ 初心者が陥りがちな3つの失敗 → 対処ルール（対応づけ）
- 失敗①：【定義/症状】 → 対処ルール：【ルール名/要点】
- 失敗②：【定義/症状】 → 対処ルール：【ルール名/要点】
- 失敗③：【定義/症状】 → 対処ルール：【ルール名/要点】
※対応が不明な箇所は「文字起こしからは不明」

④ マット氏の「3つのルール」（事実／主観を分けて）
- ルール1：【名称/目的/適用条件】
  - 事実：【動画での説明・条件・例】
  - 主観：【推奨・哲学・注意点】
- ルール2：【…】
- ルール3：【…】

⑤ エントリー・損切・利確の具体例（事実）
- エントリー条件：【シグナル条件】 ／ 確定条件：【終値/出来高/足確定 等】
- 損切ルール：【基準や距離・無ければ不明】
- 利確ルール：【基準や分割・無ければ不明】
- 参考時間軸：

⑥ 手順（最大5行のステップ）
1) チャート設定 → 2) シグナル確認 → 3) 上位足整合 → 4) エントリー/損切設定 → 5) 利確運用/記録

⑦ スイング vs ポジションの違い（動画の定義ベース）
- 定義/時間軸/保有期間/判断材料の差異を簡潔に。無ければ「文字起こしからは不明」

⑧ 注意点・リスク
- ダマシ条件／イベント跨ぎ／ボラ急変／過剰最適化 等（動画に出た範囲）

⑨ 哲学・再現のポイント（主観の扱い）
- マット氏の哲学の要点（主張／前提／限界）

⑩ 用語ミニ解説（動画に出た用語のみ）
- 例：スイングトレード＝【1行説明】、ポジショントレード＝【1行説明】

【入力：動画文字起こし】
{cleaned_text}
---文字起こし終了---
"""


def call_gemini(prompt: str) -> str:
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    return response.text


def format_as_html(title: str, md_text: str, video_url: str) -> str:
    body_html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return f"""<html><body><h2>{title}</h2><p><a href="{video_url}" target="_blank">🔗 YouTubeで見る</a></p><div>{body_html}</div></body></html>"""


def send_gmail(subject: str, html_body: str, to_email: str, attachment_path: Optional[str] = None):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds)
    message = MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject

    # 本文
    message.attach(MIMEText(html_body, "html"))

    # 添付ファイル
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            file_data = f.read()
            file_name = os.path.basename(attachment_path)

        # 添付ファイルのMIMEタイプを自動判別
        mime_type, _ = mimetypes.guess_type(attachment_path)
        mime_type = mime_type.split("/") if mime_type else ["application", "octet-stream"]

        # 添付ファイルの設定
        attachment = MIMEBase(mime_type[0], mime_type[1])
        attachment.set_payload(file_data)
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{file_name}",
        )
        message.attach(attachment)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw}
    service.users().messages().send(userId="me", body=body).execute()


@app.route("/", methods=["GET", "POST"])
def index():
    youtube_url = None
    mp3_generated = False

    if request.method == "POST":
        youtube_url = request.form.get("youtube_url")
    elif request.method == "GET":
        youtube_url = request.args.get("url")

    if not youtube_url:
        return render_template("index.html", error_message="URLが指定されていません")

    try:
        print("\n==============================")
        print(f"✅ 受信URL: {youtube_url}")
        print("==============================")

        cleaned_url = clean_youtube_url(youtube_url)

        # 既存ファイル削除
        for ext in ("*.vtt", "*.txt"):
            for file in CAPTIONS_DIR.glob(ext):
                try:
                    file.unlink()
                    print(f"🗑️ 旧ファイル削除: {file}")
                except Exception as e:
                    print(f"⚠️ ファイル削除失敗: {file} - {e}")

        vtt_path = download_captions(cleaned_url)
        title = vtt_path.stem
        cleaned = clean_text(parse_vtt(vtt_path))

        # テキスト保存
        txt_path = CAPTIONS_DIR / f"{title}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"✅ 字幕テキスト保存: {txt_path}")

        # Gemini
        prompt = create_prompt(cleaned, title, youtube_url)
        summary_md = call_gemini(prompt)

        if not summary_md:
            return "<h2>❌ Gemini要約取得に失敗しました。</h2>", 500

        # TTS処理
        summary_for_tts = extract_summary_ssml(summary_md)
        if summary_for_tts:
            mp3_generated = generate_gcp_tts_mp3(summary_for_tts, TEMP_MP3_FILE)

        # メール送信
        summary_html = markdown.markdown(summary_md, extensions=["fenced_code", "tables"])
        html_body = format_as_html(title, summary_md, cleaned_url)
        subject = f"【要約・音声完了】{title}"

        attachment_to_send = TEMP_MP3_FILE if mp3_generated and os.path.exists(TEMP_MP3_FILE) else None
        send_gmail(subject, html_body, GMAIL_TO, attachment_to_send)

        # 一時ファイル削除
        if os.path.exists(TEMP_MP3_FILE):
            os.remove(TEMP_MP3_FILE)
            print(f"🗑️ 一時TTSファイル削除: {TEMP_MP3_FILE}")

        # 結果表示
        escaped_text = cleaned.replace("<", "&lt;").replace(">", "&gt;")
        return render_template(
            "result.html",
            title=title,
            video_url=cleaned_url,
            text=escaped_text,
            summary_html=summary_html,
            has_audio=bool(attachment_to_send)
        )

    except FileNotFoundError as e:
        return f"<h2>❌ エラー発生</h2><p>字幕ダウンロードに失敗しました。</p><pre>{str(e)}</pre>", 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"<h2>❌ エラー発生</h2><pre>{str(e)}</pre>", 500


@app.route("/auth")
def auth():
    try:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
        flash("✅ Gmail認証が完了しました", "success")
        return redirect(url_for("index"))
    except Exception as e:
        return f"<h2>❌ 認証エラー</h2><pre>{str(e)}</pre>", 500


def extract_summary_ssml(output: str) -> Optional[str]:
    """Gemini出力からTTS用テキストを抽出し、SSMLに整形"""
    print("▶ TTS用SSML抽出開始")
    
    if not output or not output.strip():
        print("⚠️ 出力テキストが空です。SSML生成を中止します。")
        return None

    # フォールバック: 先頭1500文字程度を利用
    text = re.sub(r"^#{1,6}\s*", "", output, flags=re.MULTILINE)
    text = re.sub(r"`+", "", text)
    text = text.strip()[:1500]
    
    text_cleaned = re.sub(
        r'^[ \t]*[*\-+]\s*|^[ \t]*\d+\.\s*',
        '',
        text,
        flags=re.MULTILINE
    )
    text_cleaned = re.sub(r'\n\s*\n', '\n', text_cleaned).strip()
    ssml_content = text_cleaned.replace('\n', '<break time="120ms"/>')
    ssml = f"<speak>{ssml_content}</speak>"

    print(f"✅ SSML生成完了（プレビュー）:\n{ssml[:200]}...")
    return ssml


def generate_gcp_tts_mp3(text_to_read: str, output_filepath: str) -> bool:
    if not text_to_read:
        print("⚠️ TTS用テキストが空のためスキップ")
        return False

    print(f"▶ Google Cloud TTS 呼び出し開始 (voice={TTS_VOICE_NAME}, rate={TTS_SPEAKING_RATE})")

    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(ssml=text_to_read)
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name=TTS_VOICE_NAME
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=TTS_SPEAKING_RATE
        )

        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )

        with open(output_filepath, "wb") as out:
            out.write(response.audio_content)

        size = os.path.getsize(output_filepath)
        print(f"✅ TTS音声ファイル生成: {output_filepath} ({size} bytes)")
        return True

    except Exception as e:
        print(f"❌ Google Cloud TTS エラー: {e}")
        return False
