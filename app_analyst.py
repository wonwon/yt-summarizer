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
    print(f"▶ yt-dlp実行: {clean_url}")

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

    vtt_files = list(CAPTIONS_DIR.glob("*.vtt"))
    if not vtt_files:
        raise FileNotFoundError("日本語字幕ファイルが見つかりませんでした。")

    vtt_path = vtt_files[0]
    print(f"✅ VTT取得: {vtt_path}")
    return vtt_path


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

    print(f"✅ VTT解析完了: {len(text_lines)}行")
    return text_lines


def clean_text(text_lines: List[str]) -> str:
    seen, cleaned = set(), []
    for line in text_lines:
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            cleaned.append(line)
    text = "\n".join(cleaned)
    print(f"✅ 重複除去後の行数: {len(cleaned)}")
    return text


def create_prompt(cleaned_text: str, video_title: str, video_url: str) -> str:
    prompt = f"""
以下はYouTube 動画「{video_title}」の日本語字幕全文です。この内容をもとに…
【入力メタ情報】
- タイトル: {video_title}
- URL: {video_url}

プロンプト

あなたは、このチャンネルの世界観と視聴者を深く理解している **「編集長兼リサーチャー兼実務コンサルタント」** です。

これから渡すテキストは、このチャンネルで配信された動画の **文字起こし全文** です。  
テーマは一貫して「現代の仕事・学び・テクノロジー・人間のパフォーマンス」を扱う専門家インタビューです。  
この前提をふまえ、次の要件に従って整理・解説してください。

---（中略：元プロンプト本文そのまま）---

【入力：動画文字起こし】  
{cleaned_text}  
---文字起こし終了---
"""
    print("✅ Gemini送信用プロンプト生成完了")
    return prompt


def call_gemini(prompt: str) -> str:
    """Gemini API呼び出し結果をコンソールに出力"""
    print("▶ Gemini API 呼び出し開始")
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)

    if not hasattr(response, "text") or response.text is None:
        print("❌ Geminiレスポンスにtextが含まれていません")
        return ""

    print("✅ Geminiレスポンス受信")
    # 長すぎるので先頭だけ表示
    preview = response.text[:500].replace("\n", " ")
    print(f"--- Gemini出力プレビュー(先頭500文字) ---\n{preview}\n--- end ---")
    return response.text


def extract_summary_ssml(output: str) -> Optional[str]:
    print("\n=== SSML生成デバッグ ===")
    print(f"入力テキスト長: {len(output)} 文字")
    
    if not output or not output.strip():
        print("❌ 入力テキストが空")
        return None

    # セクション抽出デバッグ
    sections = {
        "sec1": None,
        "sec2": None,
        "sec4": None
    }

    for section_num in [1, 2, 4]:
        pattern_start = f"^##\\s*{section_num}[\.．]?[^\\n]*"
        pattern_next = f"^##\\s*{section_num + 1}[\.．]?[^\\n]*"
        
        match = re.search(
            pattern_start + r"\s*\n(.*?)(?=" + pattern_next + r"|\Z)",
            output,
            re.DOTALL | re.MULTILINE,
        )
        
        if match:
            sections[f"sec{section_num}"] = match.group(1).strip()
            print(f"✅ セクション{section_num}を抽出: {len(sections[f'sec{section_num}'])}文字")
        else:
            print(f"⚠️ セクション{section_num}が見つかりません")

    # SSMLビルド
    if not any(sections.values()):
        print("⚠️ セクション抽出失敗→フォールバック使用")
        fallback = output[:1500]
        print(f"フォールバックテキスト長: {len(fallback)}文字")
        text_cleaned = re.sub(
            r'^[ \t]*[*\-+]\s*|^[ \t]*\d+\.\s*',
            '',
            fallback,
            flags=re.MULTILINE
        )
    else:
        print("✅ セクション抽出成功→通常フロー")
        # セクション結合
        parts = []
        for sec_num, content in sections.items():
            if content:
                parts.append(f"セクション{sec_num[-1]}. {content}")
        
        raw_text = "\n\n".join(parts)
        print(f"結合後テキスト長: {len(raw_text)}文字")
        
        text_cleaned = re.sub(
            r'^[ \t]*[*\-+]\s*|^[ \t]*\d+\.\s*',
            '',
            raw_text,
            flags=re.MULTILINE
        )

    # 最終SSML生成
    text_cleaned = re.sub(r'\n\s*\n', '\n', text_cleaned).strip()
    ssml_content = text_cleaned.replace('\n', '<break time="120ms"/>')
    ssml = f"<speak>{ssml_content}</speak>"
    
    print(f"最終SSML長: {len(ssml)}文字")
    print("=== SSML生成完了 ===\n")
    
    return ssml



def generate_gcp_tts_mp3(text_to_read: str, output_filepath: str) -> bool:
    if not text_to_read:
        print("⚠️ TTS用テキストが空のためスキップ")
        return False

    print(f"▶ Google Cloud TTS 呼び出し開始")
    print(f"- Voice: {TTS_VOICE_NAME}")
    print(f"- Rate: {TTS_SPEAKING_RATE}")
    print(f"- 出力先: {output_filepath}")

    # 認証確認
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        print("❌ GOOGLE_APPLICATION_CREDENTIALS が設定されていません")
        return False
    
    if not os.path.exists(creds_path):
        print(f"❌ 認証ファイルが見つかりません: {creds_path}")
        return False
    
    print(f"✅ 認証ファイル確認: {creds_path}")

    try:
        print("1️⃣ TTSクライアント初期化...")
        client = texttospeech.TextToSpeechClient()
        print("✅ クライアント初期化完了")

        print("2️⃣ 入力テキスト設定...")
        print(f"入力SSML長: {len(text_to_read)} 文字")
        synthesis_input = texttospeech.SynthesisInput(ssml=text_to_read)

        print("3️⃣ 音声設定...")
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name=TTS_VOICE_NAME
        )

        print("4️⃣ 音声設定...")
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=TTS_SPEAKING_RATE
        )

        print("5️⃣ API呼び出し...")
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        print("✅ API呼び出し成功")

        print("6️⃣ 音声ファイル書き込み...")
        with open(output_filepath, "wb") as out:
            out.write(response.audio_content)

        size = os.path.getsize(output_filepath)
        print(f"✅ TTS音声ファイル生成完了: {output_filepath}")
        print(f"  - サイズ: {size:,} bytes")
        print(f"  - アクセス確認: {os.access(output_filepath, os.R_OK)}")
        return True

    except Exception as e:
        print(f"❌ Google Cloud TTS エラー詳細:")
        print(f"  - エラータイプ: {type(e).__name__}")
        print(f"  - エラーメッセージ: {str(e)}")
        import traceback
        print("  - スタックトレース:")
        print(traceback.format_exc())
        return False


def format_as_html(title: str, md_text: str, video_url: str) -> str:
    body_html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return f"""<html><body><h2>{title}</h2><p><a href="{video_url}" target="_blank">🔗 YouTubeで見る</a></p><div>{body_html}</div></body></html>"""


def send_gmail(subject: str, html_body: str, to_email: str, attachment_path: Optional[str] = None):
    """Gmail APIレスポンスをコンソールに出力"""
    print("▶ Gmail送信処理開始")

    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEMultipart()
    msg["To"] = to_email
    msg["From"] = to_email  # 実際は認証ユーザーに自動補正される
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if attachment_path and os.path.exists(attachment_path):
        try:
            ctype, encoding = mimetypes.guess_type(attachment_path)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)

            with open(attachment_path, 'rb') as fp:
                attachment = MIMEBase(maintype, subtype)
                attachment.set_payload(fp.read())

            encoders.encode_base64(attachment)
            attachment.add_header(
                'Content-Disposition',
                'attachment',
                filename=os.path.basename(attachment_path),
            )
            msg.attach(attachment)
            print(f"✅ 添付ファイル設定完了: {attachment_path}")
        except Exception as e:
            print(f"❌ 添付ファイル処理エラー: {e}")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        print("✅ Gmail送信成功")
        print(f"📨 Gmail APIレスポンス: id={result.get('id')}, threadId={result.get('threadId')}")
        if attachment_path:
            print("📎 添付ファイル付きで送信済み")
        else:
            print("⚠️ 添付ファイルなしで送信（mp3未生成またはエラー）")

        return result

    except Exception as e:
        print(f"❌ Gmail送信エラー: {e}")
        return None


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

        text_lines = parse_vtt(vtt_path)
        cleaned = clean_text(text_lines)

        txt_path = CAPTIONS_DIR / f"{title}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"✅ 字幕テキスト保存: {txt_path}")

        prompt = create_prompt(cleaned, title, youtube_url)
        summary_md = call_gemini(prompt)

        if not summary_md:
            return "<h2>❌ Gemini要約取得に失敗しました。</h2>", 500

        summary_html = markdown.markdown(summary_md, extensions=["fenced_code", "tables"])
        html_body = format_as_html(title, summary_md, cleaned_url)
        subject = f"【要約・音声完了】{title}"

        # --- TTS部分 ---
        print("\n=== TTS処理開始 ===")
        summary_for_tts = extract_summary_ssml(summary_md)
        
        if summary_for_tts:
            print(f"✅ SSML取得成功 ({len(summary_for_tts)}文字)")
            mp3_generated = generate_gcp_tts_mp3(summary_for_tts, TEMP_MP3_FILE)
            if mp3_generated:
                print("✅ MP3生成成功")
            else:
                print("❌ MP3生成失敗")
        else:
            print("❌ SSML生成失敗")
            mp3_generated = False
        
        print("=== TTS処理完了 ===\n")

        # メール送信
        attachment_to_send = TEMP_MP3_FILE if mp3_generated and os.path.exists(TEMP_MP3_FILE) else None
        if attachment_to_send:
            print(f"✅ 添付ファイル準備完了: {attachment_to_send}")
        else:
            print("⚠️ 添付ファイルなし")

        # --- Gmail送信 ---
        send_gmail(subject, html_body, GMAIL_TO, attachment_to_send)

        # --- 一時ファイル削除 ---
        if os.path.exists(TEMP_MP3_FILE):
            os.remove(TEMP_MP3_FILE)
            print(f"🗑️ 一時TTSファイル削除: {TEMP_MP3_FILE}")

        escaped_text = cleaned.replace("<", "&lt;").replace(">", "&gt;")

        return f"""
        <html>
        <head>
            <meta charset="utf-8">
            <title>{title}</title>
            <style>
                .copy-box {{
                    background: #f9f9f9;
                    padding: 1em;
                    border: 1px solid #ccc;
                    white-space: pre-wrap;
                    font-family: monospace;
                    cursor: pointer;
                }}
            </style>
        </head>
        <body style="font-family:Arial,sans-serif;line-height:1.6;">
            <h2>{title}</h2>
            <p><a href="{cleaned_url}" target="_blank">🔗 YouTubeで見る</a></p>

            <h3>🎤 字幕全文（クリックでコピー）</h3>
            <div id="copyTarget" class="copy-box" onclick="copyText()">🔘 クリックでコピー<br><br>{escaped_text}</div>

            <h3>🤖 Geminiによる要約</h3>
            <div>{summary_html}</div>

            <h3 style="color: green;">
                ✅ 処理完了: コンソールログに各ステップのレスポンスを出力しました。<br>
                （メール送信: 実施 / 添付音声: { 'あり' if attachment_to_send else 'なし' }）
            </h3>

            <script>
            function copyText() {{
                const element = document.getElementById("copyTarget");
                const text = element.innerText.replace(/^🔘.*\\n+/, "");
                navigator.clipboard.writeText(text).then(function() {{
                    alert("✅ コピーしました！");
                }}, function(err) {{
                    alert("❌ コピーに失敗しました: " + err);
                }});
            }}
            </script>
        </body>
        </html>
        """

    except FileNotFoundError as e:
        return f"<h2>❌ エラー発生</h2><p>字幕ダウンロードに失敗しました。</p><pre>{str(e)}</pre>", 500
    except Exception as e:
        print("\n=== エラー詳細 ===")
        import traceback
        traceback.print_exc()
        print("==================\n")
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


@app.route("/index.html")
def render_index():
    return """
    <!doctype html>
    <title>YouTube Gemini 要約ツール</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        form { margin-top: 20px; }
        input[type="text"] { padding: 10px; width: 400px; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 10px 15px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background-color: #0056b3; }
        .error { color: red; }
    </style>
    <body>
        <h2>YouTube Gemini 要約・音声化ツール</h2>
        <p>YouTube URLを入力し、要約と音声ファイルをメールで受け取ります。</p>
        <form method="POST" action="/">
            <input type="text" name="youtube_url" placeholder="YouTube URLを入力 (例: https://www.youtube.com/watch?v=...)" required>
            <button type="submit">要約・音声生成</button>
        </form>
        <p>※事前に `credentials.json` を配置し、/auth へのアクセスで認証を完了させてください。</p>
    </body>
    </html>
    """
