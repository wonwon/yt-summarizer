import base64
import os
import re
import subprocess
import sys
from email.mime.text import MIMEText
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import google.generativeai as genai
import markdown  # ★追加：Markdown→HTML変換用
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ===============================
# 事前準備
# ===============================
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_TO = os.getenv("GMAIL_TO")  # .envで送信先指定推奨
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

if not GEMINI_API_KEY:
    print("❌ .envファイルにGEMINI_API_KEYが定義されていません")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)

CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)


# ===============================
# 字幕ダウンロード（yt-dlp）
# ===============================
def download_captions(youtube_url: str) -> Path:
    print("📥 字幕をダウンロード中...\n")
    clean_url = youtube_url.split("?")[0]
    result = subprocess.run(
        [
            "yt-dlp",
            "--write-auto-sub",
            "--sub-lang", "ja",
            "--skip-download",
            "--output", str(CAPTIONS_DIR / "%(title)s [%(id)s].%(ext)s"),
            clean_url
        ],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print("❌ エラー発生:", result.stderr)
        sys.exit(1)

    for file in CAPTIONS_DIR.glob("*.vtt"):
        return file

    print("❌ 字幕ファイル（.vtt）が見つかりません。")
    sys.exit(1)


# ===============================
# VTTパース
# ===============================
def parse_vtt(vtt_path: Path) -> List[str]:
    with vtt_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    text_lines = []
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


# ===============================
# 重複削除・整形
# ===============================
def clean_text(text_lines: List[str]) -> str:
    seen = set()
    cleaned_lines = []
    for line in text_lines:
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


# ===============================
# プロンプト作成
# ===============================
def create_prompt(cleaned_text: str, video_title: str) -> str:
    return f"""
以下はYouTube 動画「{video_title}」の日本語字幕全文です。これを要約し、推奨リソースを含むMarkdownとJSONの両方で出力してください。

あなたは「要約×構造化」に長けたプロ編集者です。対象はYouTube動画の“整形済み”文字起こし。

【入力メタ情報】
- タイトル: {video_title}
- URL: {video_url}

1. 【要約（箇条書きで簡潔に）】
   - 何について語られているか（テーマ・主題）
   - 重要なポイント・事実・データなど

2. 【分析・考察】
   - なぜそのような現象が起きているのか（背景や因果関係）
   - 投資家視点や社会的影響などの洞察があれば加えてください

3. 【用語解説】
   - テキスト内に出てくる専門用語・略語などを簡単に補足してください
   - 解説は初心者でもわかるように短くまとめてください

※構造的に整理して、伝わりやすくまとめてください。

---文字起こし開始---
{cleaned_text}
---文字起こし終了---
"""

# 元の詳細プロンプト（コメントアウト解除：文字列変数として保存）
#ORIGINAL_DETAILED_PROMPT = """あなたはプロの技術ライター兼トレーナーです。以下の「文字起こし」をもとに、ハウツー動画の要点をわかりやすく、実行可能な形で出力してください。出力は「Markdown（説明用）」と「JSON（機械処理用）」の両方を返してください。重要なルール：
#
# 1. 【要約（箇条書きで簡潔に）】
#    - 何について語られているか（テーマ・主題）
#    - 重要なポイント・事実・データなど
#
# 2. 【分析・考察】
#    - なぜそのような現象が起きているのか（背景や因果関係）
#    - 投資家視点や社会的影響などの洞察があれば加えてください
#
# 3. 【用語解説】
#    - テキスト内に出てくる専門用語・略語などを簡単に補足してください
#    - 解説は初心者でもわかるように短くまとめてください
#
# ※構造的に整理して、伝わりやすくまとめてください。

# ===============================
# Geminiで要約取得
# ===============================
def call_gemini(prompt: str) -> str:
    print("🤖 Gemini に要約を依頼中...\n")
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    print("✅ Gemini 要約取得完了\n")
    return response.text


# ===============================
# Geminiレスポンス → HTML整形（Markdown対応）
# ===============================
def format_as_html(title: str, md_text: str) -> str:
    # Markdown→HTML変換
    body_html = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height:1.6;">
        <h2>{title}</h2>
        <div>{body_html}</div>
    </body>
    </html>
    """


# ===============================
# Gmail APIで送信
# ===============================
def send_gmail(subject: str, html_body: str, to_email: str):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds)

    message = MIMEText(html_body, "html")
    message["to"] = to_email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    message_body = {"raw": raw}
    sent = service.users().messages().send(userId="me", body=message_body).execute()
    print("📤 メール送信完了:", sent["id"])


# ===============================
# メイン実行
# ===============================
def main():
    if len(sys.argv) != 2:
        print("Usage: python youtube_to_gemini_mailer.py <YouTube URL>")
        sys.exit(1)

    # 🔻 追加：既存の .vtt ファイルを削除
    for file in CAPTIONS_DIR.glob("*.vtt"):
        try:
            file.unlink()
        except Exception as e:
            print(f"⚠️ 削除失敗: {file.name} - {e}")

            
    youtube_url = sys.argv[1]
    vtt_path = download_captions(youtube_url)
    video_title = vtt_path.stem
    text_lines = parse_vtt(vtt_path)
    cleaned_text = clean_text(text_lines)
    prompt = create_prompt(cleaned_text, video_title, youtube_url, audience="AI初学者", length="600字", max_links=5)
    summary_md = call_gemini(prompt)
    html_body = format_as_html(video_title, summary_md)
    subject = f"【要約完了】{video_title}"
    send_gmail(subject, html_body, GMAIL_TO)


if __name__ == "__main__":
    main()