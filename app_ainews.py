import base64
import os
import re
import subprocess
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
import markdown
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from google.cloud import texttospeech
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

app = Flask(__name__)
app.secret_key = "your_secret_key"

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_TO = os.getenv("GMAIL_TO")
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

genai.configure(api_key=GEMINI_API_KEY)
CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)

from urllib.parse import parse_qs, urlparse


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

あなたは「要約×構造化」に長けたプロ編集者です。対象はYouTube動画の“整形済み”文字起こし。
あなたは「AIニュース解説」を行うプロ編集者です。対象はYouTube動画の“整形済み”文字起こし。
目的：動画のストーリーを簡潔に要点化し、事実と意見を分離、実務/投資判断に使える形で提示する。
前提：タイムスタンプは不要。専門用語の詳細解説は今回不要（見出しだけ列挙）。関連情報は一次情報中心でリンク付き。

【入力メタ情報】
- タイトル: {video_title}
- URL: {video_url}
- 公開時期/文脈（分かる範囲で）: 
- 想定読者: AIエンジニア、IT企業の意思決定者、投資家
- 出力言語/トーン: 日本語（敬語7:カジュアル3）
- 要約の長さ: 600〜800字程度
- 関連リンク件数（目安）: ３

【厳守ルール（AIニュース版）】
1) 事実/主張/推測を明確に分ける。「（事実）」「（主張）」「（推定）」のタグ可。
2) 不明点は「（不明）」と書く。憶測で補わない。
3) 数値・日付・名称は誤記厳禁。単位と基準時点を明示。
4) 外部情報は本文と区別し「補足:」で始め、情報源の種別（公式Doc/ニュース/ブログ/論文）を括弧で付す。
5) リンクは一次情報を最優先。日本語があれば日本語→英語の順。出典が曖昧ならリンクを出さない。
6) 政策・価格・仕様・モデル番号は変動するため、断定を避け「～と説明」「～と報告」など準拠表現。
7) 専門用語は“見出しのみ”列挙（詳細は別依頼で追記する前提）。

【出力フォーマット】
1. タイトル（要約版）／12字以内
2. TL;DR（3行以内）
3. ストーリー要点（時系列で5～9項目）
4. 重要ファクト（数値/期日/発表主体/対象地域）
5. 影響と含意（日本/グローバル/製造現場/投資家 など必要な軸で）
6. 反対意見・未確定点・リスク（3～6項目）
7. 関連リンク（3件）— [名称] – 1行要点（種別：公式Doc/ニュース/ブログ/論文、可能なら公開/更新日）
8. 用語見出し（5～10語：解説は不要）
9. 次アクション（Today/This Weekで具体）


---文字起こし開始---
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
    """メール送信（添付ファイル対応）"""
    print("\n=== Gmail送信開始 ===")
    
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds)
    
    # MIMEMultipartに変更
    message = MIMEMultipart()
    message["to"] = to_email
    message["subject"] = subject
    
    # HTML本文を追加
    message.attach(MIMEText(html_body, "html"))
    
    # 添付ファイルがあれば追加
    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase("audio", "mpeg")
                part.set_payload(attachment.read())
                
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(attachment_path)}",
            )
            message.attach(part)
            print(f"✓ 添付ファイル設定完了: {attachment_path}")
        except Exception as e:
            print(f"⚠️ 添付ファイル処理エラー: {e}")
    
    # エンコードして送信
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    try:
        sent_message = service.users().messages().send(
            userId="me",
            body={"raw": encoded_message}
        ).execute()
        print(f"✓ メール送信完了: message_id={sent_message['id']}")
        return sent_message
    except Exception as e:
        print(f"❌ メール送信エラー: {e}")
        raise


def extract_summary_ssml(output: str) -> Optional[str]:
    """Gemini出力からTTS用テキストを抽出し、SSMLに整形"""
    print("\n=== TTS用SSML抽出開始 ===")
    
    if not output or not output.strip():
        print("❌ エラー: 出力テキストが空")
        return None

    try:
        # マークダウン記法を除去
        text = re.sub(r"^#{1,6}\s*", "", output, flags=re.MULTILINE)
        print("✓ マークダウン見出し除去完了")
        
        text = re.sub(r"`+", "", text)
        print("✓ コードブロック記法除去完了")
        
        text = text.strip()[:1500]  # 長すぎる場合は先頭1500文字まで
        print(f"✓ テキスト長調整完了（{len(text)}文字）")
        
        # 箇条書きなどの記号を除去
        text_cleaned = re.sub(
            r'^[ \t]*[*\-+]\s*|^[ \t]*\d+\.\s*',
            '',
            text,
            flags=re.MULTILINE
        )
        print("✓ 箇条書き記号除去完了")
        
        # 空行の正規化
        text_cleaned = re.sub(r'\n\s*\n', '\n', text_cleaned).strip()
        print("✓ 空行正規化完了")
        
        # SSML変換
        ssml_content = text_cleaned.replace('\n', '<break time="120ms"/>')
        ssml = f"<speak>{ssml_content}</speak>"
        print("✓ SSML生成完了")
        
        # プレビュー出力（先頭200文字）
        print("\n🔍 SSML プレビュー:")
        print(f"{ssml[:200]}...")
        
        return ssml

    except Exception as e:
        print(f"❌ SSML生成エラー: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def generate_gcp_tts_mp3(text_to_read: str, output_filepath: str) -> bool:
    """Google Cloud TTSを使用して音声ファイルを生成"""
    print("\n=== Google Cloud TTS 処理開始 ===")
    
    if not text_to_read:
        print("❌ エラー: 入力テキストが空のため中止")
        return False

    try:
        print(f"▶ 設定値確認:")
        print(f"  - 音声: {TTS_VOICE_NAME}")
        print(f"  - 速度: {TTS_SPEAKING_RATE}")
        print(f"  - 出力先: {output_filepath}")
        
        # 認証確認
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_path or not os.path.exists(creds_path):
            print("❌ エラー: Google認証情報が見つかりません")
            print(f"  GOOGLE_APPLICATION_CREDENTIALS={creds_path}")
            return False
        print("✓ 認証情報確認OK")

        # クライアント初期化
        client = texttospeech.TextToSpeechClient()
        print("✓ TTSクライアント初期化完了")

        # 入力設定
        synthesis_input = texttospeech.SynthesisInput(ssml=text_to_read)
        print("✓ 入力設定完了")

        # 音声設定
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name=TTS_VOICE_NAME
        )
        print("✓ 音声設定完了")

        # 音声フォーマット設定
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=TTS_SPEAKING_RATE
        )
        print("✓ 音声フォーマット設定完了")

        # API呼び出し
        print("▶ TTS API リクエスト送信...")
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        print("✓ TTS API レスポンス受信")

        # ファイル保存
        with open(output_filepath, "wb") as out:
            out.write(response.audio_content)

        # 結果確認
        size = os.path.getsize(output_filepath)
        print(f"✅ 音声ファイル生成完了: {output_filepath}")
        print(f"  サイズ: {size:,} bytes")
        
        return True

    except Exception as e:
        print(f"❌ TTS処理エラー: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


# グローバル設定を上部に移動
TEMP_MP3_FILE = CAPTIONS_DIR / "summary.mp3"
TTS_VOICE_NAME = "ja-JP-Wavenet-B"
TTS_SPEAKING_RATE = 1.8

@app.route("/", methods=["GET", "POST"])
def index():
    youtube_url = None
    mp3_generated = False

    if request.method == "POST":
        youtube_url = request.form.get("youtube_url")
    elif request.method == "GET":
        youtube_url = request.args.get("url")

    if not youtube_url:
        return """
            <h2>YouTube Gemini 要約ツール</h2>
            <form method="POST">
                <input type="text" name="youtube_url" placeholder="YouTube URLを入力" style="width:400px;">
                <button type="submit">送信</button>
            </form>
            <p style="color:red;">※URLが指定されていません</p>
        """

    try:
        print("\n=== 処理開始 ===")
        print(f"✅ 受信URL: {youtube_url}")

        # URL整形（v=だけ抽出）を必ず通す
        cleaned_url = clean_youtube_url(youtube_url)

        # vtt と txt を削除
        for ext in ("*.vtt", "*.txt"):
            for file in CAPTIONS_DIR.glob(ext):
                try:
                    file.unlink()
                    print(f"🗑️ 旧ファイル削除: {file}")
                except Exception as e:
                    print(f"⚠️ ファイル削除失敗: {file} - {e}")

        vtt_path = download_captions(cleaned_url)
        title = vtt_path.stem
        text_lines = parse_vtt(vtt_path)
        cleaned = clean_text(text_lines)

        # テキスト保存
        txt_path = CAPTIONS_DIR / f"{title}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"✅ 字幕テキスト保存: {txt_path}")

        # Gemini処理
        prompt = create_prompt(cleaned, title, youtube_url)
        summary_md = call_gemini(prompt)
        
        if not summary_md:
            return "<h2>❌ Gemini要約取得に失敗しました。</h2>", 500

        # TTS処理
        print("\n=== 音声生成処理開始 ===")
        summary_for_tts = extract_summary_ssml(summary_md)
        
        if summary_for_tts:
            print("▶ TTS処理実行")
            mp3_generated = generate_gcp_tts_mp3(summary_for_tts, TEMP_MP3_FILE)
            print(f"TTS処理結果: {'成功' if mp3_generated else '失敗'}")
        else:
            print("⚠️ SSML生成失敗のためTTS処理をスキップ")
            mp3_generated = False

        # HTML生成
        summary_html = markdown.markdown(summary_md, extensions=["fenced_code", "tables"])
        html_body = format_as_html(title, summary_md, cleaned_url)
        
        # メール送信
        subject = f"【要約・音声完了】{title}"
        attachment_path = TEMP_MP3_FILE if mp3_generated and os.path.exists(TEMP_MP3_FILE) else None
        send_gmail(subject, html_body, GMAIL_TO, attachment_path)

        # 一時ファイルのクリーンアップ
        if os.path.exists(TEMP_MP3_FILE):
            os.remove(TEMP_MP3_FILE)
            print(f"🗑️ 一時TTSファイル削除: {TEMP_MP3_FILE}")

        # 結果画面生成
        escaped_text = cleaned.replace("<", "&lt;").replace(">", "&gt;")
        return render_template(
            "result.html",
            title=title,
            video_url=cleaned_url,
            text=escaped_text,
            summary_html=summary_html,
            has_audio=bool(attachment_path)
        )

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
