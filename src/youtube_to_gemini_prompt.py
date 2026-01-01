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

# Gemini APIキー（フォールバック対応）
GEMINI_API_KEY_PRIMARY = os.getenv("GEMINI_API_KEY_PRIMARY")
GEMINI_API_KEY_FALLBACK = os.getenv("GEMINI_API_KEY_FALLBACK")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # 後方互換性のため

GMAIL_TO = os.getenv("GMAIL_TO")  # .envで送信先指定推奨
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# APIキーのバリデーション
if not (GEMINI_API_KEY_PRIMARY or GEMINI_API_KEY):
    print("❌ GEMINI_API_KEY_PRIMARY または GEMINI_API_KEY が設定されていません")
    sys.exit(1)

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
目的：動画のストーリーを簡潔に要点化し、実務に使える形で再構成する。関連情報は一次情報を中心にリンク付きで紹介する。専門用語の詳解は今回は不要（見出しの列挙だけ）。※タイムスタンプは不要。

【入力メタ情報】
- タイトル: {video_title}
- URL: {video_url}
- 想定読者: {audience}
- 要約の長さ: {length}
- 関連リンク件数: {max_links}

【厳守ルール（改）】
1) 憶測で断定しない。不明点は「（不明）」、推定は「（推定）」と明示。
2) 引用は必要最小限（10〜40字）で原文どおり。改変しない。
3) コード／コマンド／数値は誤記厳禁。単位も明示（%, ms, USD, JPY など）。
4) 外部情報は「補足:」として本文と区別。原典タイプを括弧で明記（公式Doc／ニュース／ブログ／論文）。
5) リンクは一次情報を優先し、日本語がある場合は日本語→英語の順で提示。可能なら公開日または更新日も併記。
6) 専門用語は「用語見出しのみ」を列挙。詳細解説は“リクエストがあったら追記”とする。
7) 語り口は日本語、敬語7割＋カジュアル3割。冗長表現は避ける。

【出力フォーマット】
1. タイトル（要約版）／12字以内
2. TL;DR（3行以内）
3. ストーリー要点（5〜9項目・箇条書き）※動画の流れが分かる並びで
4. 実務に効くポイント（3〜7項目：再現性／前提条件／落とし穴）
5. もし実装なら最小手順（番号付き・必要ツール／設定の最小例があれば1つ）
6. 関連リンク（{max_links}件）：[名称] – 要点1行（種別：公式Doc/ニュース/ブログ/論文 等、公開/更新日）
7. 用語見出しリスト（5〜10語：解説は後で追記する前提）
8. 次アクション（Today／This Week で具体）

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
    """
    Gemini APIを呼び出し、エラー時に自動的にフォールバックAPIに切り替える
    """
    model_name = "gemini-2.5-flash"
    
    # APIキーのリストを作成（優先順位順）
    api_keys = []
    if GEMINI_API_KEY_PRIMARY:
        api_keys.append(("PRIMARY (無料枠)", GEMINI_API_KEY_PRIMARY))
    if GEMINI_API_KEY_FALLBACK:
        api_keys.append(("FALLBACK (有料枠)", GEMINI_API_KEY_FALLBACK))
    
    # 後方互換性: 新しいキーが設定されていない場合は従来のキーを使用
    if not api_keys and GEMINI_API_KEY:
        api_keys.append(("DEFAULT", GEMINI_API_KEY))
    
    # 各APIキーで順番に試行
    last_error = None
    for key_name, api_key in api_keys:
        try:
            print(f"🤖 Gemini API呼び出し中 ({key_name}, Model: {model_name})")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            print(f"✅ Gemini要約取得完了 ({key_name})")
            return response.text
        
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ {key_name} でエラー発生: {error_msg}")
            last_error = e
            
            # 次のAPIキーがある場合は続行、なければエラーを投げる
            if api_keys.index((key_name, api_key)) < len(api_keys) - 1:
                print(f"🔄 次のAPIキーでリトライします...")
                continue
            else:
                # すべてのAPIキーで失敗
                print(f"❌ すべてのAPIキーで失敗しました")
                raise last_error
    
    # ここには到達しないはずだが、念のため
    raise RuntimeError("Gemini API呼び出しに失敗しました")


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
    subject = f"【YT要約】{video_title}"
    send_gmail(subject, html_body, GMAIL_TO)


if __name__ == "__main__":
    main()