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
    # 優先順位: ja > en > 他
    candidates = list(CAPTIONS_DIR.glob("*.vtt"))
    if not candidates:
        return None

    # .ja.を含むファイルがあればそれを返す
    for p in candidates:
        if ".ja." in p.name:
            return p
            
    # なければ .en. を探す
    for p in candidates:
        if ".en." in p.name:
            return p

    # それもなければ最初に見つかったもの
    return candidates[0]


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
    return f"""以下はYouTube 動画「{video_title}」の文字起こし全文です。この内容をもとに…

あなたは「AIハウツー動画のテクニカル解説者」です。以下の文字起こしを分析し、
“ワークフロー（手順）”を中心に、実務で再現できる形で要約してください。

【入力メタ情報】
- タイトル: {video_title}
- URL: {video_url}
【目的】
- 動画の内容を、誰でも再現できる「手順書」と「チェックリスト」に変換する。
- 手順の前提条件・必要ツール・設定値・分岐・エラー対処まで整理する。

【出力フォーマット】
1) 要約（3行）
2) 全体像（1文）
3) 再現手順（ステップ式・番号付き）
   - 各ステップ：目的 / 操作手順 / 具体例（コマンド・UI操作・設定値） / 成功判定 / 失敗時の対処
4) 使うツール・モデル・API・拡張機能一覧（名称 / 役割 / 重要設定）
5) ワークフローマップ（Mermaidフローチャート）
6) よくある詰まりポイントと回避策（箇条書き）
7) 検証チェックリスト（最小再現～完成）
8) 応用例・スケールアップ案（3個）
9) 用語ミニ解説（初心者向け、各30字以内）
10) 出典・参照（動画タイトル/チャンネル名/公開日。外部リンクは「公式ドキュメント優先」）※不明は「要追加」

【制約とルール】
- 事実 / 推定 / ベストプラクティス を明確にラベル付け（[事実] / [推定] / [BP]）
- 数値・パラメータ・ファイル名は原文を優先。なければ「要追加」と明記し、安易に補完しない
- 専門用語は中学生にも通じる一言を添える（用語ミニ解説へ）
- 冗長な前置き禁止。結論 → 手順 → 注意点の順で簡潔に
- 日本語、敬体
- 表やコードはコピーしやすい書式で

【動画の前提情報（わかる範囲で抽出して明記）】
- 対象者レベル / OSやツール前提 / 想定ユースケース / 成果物

【ワークフローマップ（Mermaid）記法の例】
```mermaid
flowchart TD
  Start([開始]) --> A[準備: 環境/APIキー設定]
  A --> B[データ取得]
  B --> C[分岐：条件を満たすか]
  C -- Yes --> D[処理/変換]
  C -- No --> E[前処理/再試行]
  D --> F[検証/評価]
  F --> End([完了])
```

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
        for ext in ("*.vtt", "*.txt"):
            for file in CAPTIONS_DIR.glob(ext):
                try:
                    file.unlink()
                except Exception as e:
                    print(f"⚠️ ファイル削除に失敗しました: {file} - {e}")

        vtt_path = download_captions(cleaned_url)
        title = vtt_path.stem
        text_lines = parse_vtt(vtt_path)
        cleaned = clean_text(text_lines)

        # === ここから追記 ===
        txt_path = CAPTIONS_DIR / f"{title}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"✅ 字幕テキストを保存しました: {txt_path}")
        # === ここまで追記 ===

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
