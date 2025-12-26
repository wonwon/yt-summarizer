import base64
import os
import re
import subprocess
from email.mime.text import MIMEText
from pathlib import Path
from typing import List

import google.generativeai as genai
import markdown
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from utils.subtitle import get_subtitle

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
            "ja,en",
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
    return f"""
# ロール
あなたは「Google Cloud」をわかりやすく解説する日本語の技術ライターです。
対象は中学生〜初心者。専門用語は本文では噛み砕き、最後に用語集で説明します。

# 入力
- 動画タイトル: {video_title}
- 動画URL: {video_url}
- チャンネル: Google Cloud Tech
- 文字起こし（英語/日本語どちらでも可）:
{cleaned_text}

# 目的
1) 動画の主張と重要ポイントを、結論先出しで日本語要約（300〜600字）。
2) 実践方法を「ステップ式」で整理（3〜8ステップ）。
3) 初心者がつまずく注意点/チェックリストを追加。
4) 公式/一次情報の参照先（Google Cloud公式ドキュメント等）を列挙。
5) 専門用語の簡潔な用語集（各30〜80字）を最後にまとめる。

# 出力フォーマット（この順番・見出し固定）
【要約（日本語）】
- （結論→理由→効果の順で、300〜600字）

【ステップ式ハウツー】
1. （短文で命令形）
2. …
- ポイント：（補足があれば箇条書き可）

【チェックリスト／注意点】
- （落とし穴、前提条件、費用や権限、地域/リージョン注意 など）

【関連ドキュメント】
- タイトル — URL
- タイトル — URL

【用語集】
- 用語: 説明（30〜80字）
- 用語: 説明（30〜80字）

# 厳守ルール
- 文体：敬体（です・ます）／短文中心／比喩は簡単でOK。
- 数字・固有名詞・製品名は正確に。推測はしない。出典が曖昧な内容は書かない。
- 本文中に専門用語が出たら、その場では噛み砕き、最後に【用語集】で再説明。
- 「手順」は端末/環境に依存しない最小公倍数で書く（CLIやコンソールUIどちらでも再現可能な粒度に）。
- 英語の固有語は原語を併記してもよい（例：永続ディスク（Persistent Disk））。
- ソースリンクは、できる限りGoogle Cloud公式（product docs / tutorials / samples）を優先。
- 文字起こしが不完全でも、断定が難しい箇所は「（動画ではこの点が明確でないため、実務ではドキュメントを確認）」のように保守的に記述。

# 実行
上記フォーマットに従い、完全な日本語出力を作成してください。
""".strip()


def call_gemini(prompt: str) -> str:
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    return response.text


def format_as_html(title: str, md_text: str, video_url: str) -> str:
    body_html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return f"""<html><body><h2>{title}</h2><p><a href="{video_url}" target="_blank">🔗 YouTubeで見る</a></p><div>{body_html}</div></body></html>"""


def send_gmail(subject: str, html_body: str, to_email: str):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds)
    message = MIMEText(html_body, "html")
    message["to"], message["subject"] = to_email, subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw}
    service.users().messages().send(userId="me", body=body).execute()


@app.route("/", methods=["GET", "POST"])
def index():
    youtube_url = None

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
        print("✅ 受け取ったURL:", youtube_url)

        # URL整形（v=だけ抽出）を必ず通す
        cleaned_url = clean_youtube_url(youtube_url)

        # vtt と txt を削除
        for pattern in ("*.vtt", "*.txt"):
            for file in CAPTIONS_DIR.glob(pattern):
                try:
                    file.unlink()
                except Exception as e:
                    print(f"⚠️ ファイル削除に失敗しました: {file} - {e}")

        cleaned, title, vtt_path = get_subtitle(cleaned_url)
        prompt = create_prompt(cleaned, title, youtube_url)
        summary_md = call_gemini(prompt)
        summary_html = markdown.markdown(summary_md, extensions=["fenced_code", "tables"])
        html_body = format_as_html(title, summary_md, cleaned_url)
        subject = f"【要約完了】{title}"
        send_gmail(subject, html_body, GMAIL_TO)

        vtt_html = (
            "<pre style='background:#f9f9f9; padding:1em; border:1px solid #ccc; "
            "white-space:pre-wrap; font-family:monospace;'>"
            f"{cleaned.replace('<', '&lt;').replace('>', '&gt;')}"
            "</pre>"
        )

        # 字幕テキスト（HTMLエスケープ済み）
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

            <script>
            function copyText() {{
                const element = document.getElementById("copyTarget");
                const text = element.innerText.replace(/^🔘.*\\n+/, "");  // 冒頭のボタンは除去
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
