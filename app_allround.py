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

あなたは「要約×構造化」に長けたプロのコンテンツ・アナリスト兼編集者です。
対象はYouTube動画の“整形済み”文字起こしです。

【入力メタ情報】
- タイトル: {video_title}
- URL: {video_url}

以下は動画「{video_title}」の日本語文字起こし全文です。
この動画の内容を、視聴者が「次のアクション」や「深い理解」に繋げられる形で整理してください。

【入力：動画文字起こし】
{cleaned_text}
---文字起こしここまで---

# あなたの役割
あなたは「客観的かつ論理的なリサーチライター」です。
動画のジャンル（ビジネス、ニュース、教育、レビュー、エンタメ等）を瞬時に判断し、
ノイズを削ぎ落としつつ、事実・意見・文脈を整理して出力してください。

# 出力条件（重要）
- 日本語で出力する
- 専門知識がない人にもわかる平易な言葉で書く
- 結論 → 理由 → 補足 の順で整理する
- 動画内の「主観（発信者の意見）」と「客観的事実」を明確に区別して書く
- 不明な点は推測せず「文字起こしからは不明」と書く

# 出力フォーマット

① 動画全体の要約（3〜7行）
- 箇条書きではなく短い段落で、「この動画は一言でいうと何か？」を説明。
- 動画のジャンル、対象視聴者、解決しようとしている課題を含める。

② 要点リスト（構造的整理）
- 動画の主要トピックを論理的にグルーピングして箇条書き
- ジャンルに合わせて項目名を調整してください：
  - 【ビジネス/ニュースの場合】：背景、現状、問題点、解決策、影響範囲
  - 【レビュー/比較の場合】：スペック、メリット、デメリット、価格/コスパ、競合比較
  - 【ハウツー/教育の場合】：準備するもの、手順/ステップ、コツ、注意点

③ 発信者が一番伝えたいこと（コアメッセージ）
- この動画の「結論」もしくは「視聴者が持ち帰るべき一番のメッセージ」を1〜3行で要約
- 「〜と主張している」「〜を推奨している」など、発信者の立場を明確にする。

④ 実践・判断のための重要ポイント整理
- 視聴者が実生活や仕事で活用できる形で整理してください：
  - 具体的なアクションプラン・ToDo
  - 判断基準（ポジティブ要素 / ネガティブ要素）
  - 重要な数値・データ・期間
  - 紹介されたツール・商品・サービス名

⑤ 分析・考察に使える観点（クリティカル・シンキング）
動画内容を鵜呑みにせず、視聴者が自分で考えるための視点を提供してください：
- 「この主張の根拠は十分か？（データ元の信頼性）」
- 「あえて語られていないリスクやデメリットは何か？」
- 「他の視点（対立意見や別のアプローチ）はあるか？」
- 「特定のバイアス（宣伝、ポジショントーク等）が含まれていないか？」

⑥ 想定シナリオ・応用パターン（3パターン）
動画内容をもとに、視聴者の状況に応じた活用イメージや将来予測を整理してください。
- パターンA（積極活用/楽観）：全て取り入れた場合、最適条件の場合の効果
- パターンB（慎重検討/現実）：リスクを考慮した場合、一般的条件での着地点
- パターンC（別視点/注意）：この動画の内容が当てはまらないケース、逆効果になるケース

【用語解説】
   - テキスト内に出てくる専門用語・略語・スラングなどを簡単に補足してください
   - 初心者でもわかるように短くまとめてください

【追加タスク：関連リンク・参照情報抽出】
動画内で言及された「具体的な固有名詞」があれば抽出・整理してください。
（商品名、書籍名、Webサイト、参照ニュース、アプリ名など）
もしURLが特定できない場合は「検索ワード：〇〇」と記載してください。

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
