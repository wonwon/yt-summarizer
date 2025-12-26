import base64
import json
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
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- 設定 ---
app = Flask(__name__)
app.secret_key = "your_secret_key_tsukkomi"

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_TO = os.getenv("GMAIL_TO")
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# 明示的に gemini-2.5-flash-lite を使用
MODEL_NAME = "gemini-2.5-flash-lite"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)

# -----------------
# Utility Functions (Copied/Adapted from app.py for standalone functionality)
# -----------------

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

def download_captions(youtube_url: str) -> Optional[Path]:
    clean_url = clean_youtube_url(youtube_url)
    # クッキーファイルがあれば使用
    cookies_args = []
    if os.path.exists("cookies.txt"):
        cookies_args = ["--cookies", "cookies.txt"]

    cmd = [
        "yt-dlp",
        "--write-auto-sub",
        "--sub-lang", "ja,en",
        "--skip-download",
        "--output", str(CAPTIONS_DIR / "%(title)s [%(id)s].%(ext)s"),
        *cookies_args,
        clean_url,
    ]

    try:
        subprocess.run(cmd, check=True)
        # 優先順位: .ja.vtt > .en.vtt > others
        candidates = list(CAPTIONS_DIR.glob("*.vtt"))
        if not candidates:
            return None
        
        # タイトル等でフィルタリングすべきだが、今回は直近の更新を見るか、
        # シンプルにglobで見つかったもののうち、clean_urlに関連しそうなものを探す実装が理想。
        # ここでは簡易的に、一番新しいファイルを返すことにする（単一ユーザー想定）
        candidates.sort(key=os.path.getmtime, reverse=True)
        
        for p in candidates:
            if ".ja." in p.name:
                return p
        return candidates[0] if candidates else None
    except subprocess.CalledProcessError as e:
        print(f"Error downloading captions: {e}")
        return None

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

# -----------------
# Core Analysis Logic
# -----------------

def analyze_tsukkomi(text: str, title: str) -> str:
    """
    Gemini 2.5 Flash Lite を使用してツッコミ・妄想・ワードセンスを分析する
    """
    print(f"🤖 Analyzing with model: {MODEL_NAME}")
    
    prompt = f"""
以下はYouTube動画「{title}」の文字起こしテキストです。
この動画は、投稿者の独特な言語センス、激しいツッコミ、あるいは広がりすぎる妄想トークが特徴的である可能性があります。

あなたのタスクは、このテキストから「笑えるフレーズ」「パワーワード」「独特なツッコミ」「妄想トーク」を抽出し、
それがなぜ面白いのか、**「なにとかけているのか（元ネタ、言葉遊び、文脈）」**を深く分析して解説することです。

【分析ルール】
1. **Gemini 2.5 Flash Lite** の能力を活かし、高速かつ鋭い分析を行ってください。
2. ただの抜き出しではなく、「解説」に重きを置いてください。
3. 以下のカテゴリに分類してください：
   - **【ワードセンス】**: 独特な造語、言い回し、語彙選択の妙。
   - **【ツッコミ】**: 鋭い指摘、比喩を使ったツッコミ。
   - **【妄想】**: 事実から飛躍しすぎたストーリー、ありえない仮定。
   - **【知識/教養】**: 専門用語やマニアックなネタを絡めたボケ。

【出力フォーマット（Markdown形式）】
## 🎬 動画のバイブス分析
（この動画全体のテンションや、投稿者のキレ具合を3行程度で総評してください）

## 🤣 珠玉のフレーズ＆分析リスト

| フレーズ / パワーワード | 分類 | なにとかけているか・解説 |
| :--- | :---: | :--- |
| 「(例) 人生の走馬灯がRTAまたいになってる」 | ワードセンス | **RTA（リアルタイムアタック）**とかけている。人生の振り返りが異常に速く雑であることを、ゲーム用語を用いて表現した秀逸な比喩。 |
| 「(例) 前世がハムスターの回し車だったのかもしれない」 | 妄想 | 堂々巡りの状況を、ハムスターの回し車という具体的かつ悲哀のある対象に転生させることで笑いを誘っている。 |
... (抽出できた分だけ列挙)...

## 💡 総評：ここが沼ポイント
（このチャンネル/動画の言語センスがなぜ中毒性を持つのか、分析結果をまとめてください）

---
【対象テキスト】
{text[:20000]} 
(※テキストが長すぎる場合は適宜カットされています)
"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"エラーが発生しました: {str(e)}"

# -----------------
# Routes
# -----------------

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        youtube_url = request.form.get("youtube_url")
        if not youtube_url:
            return render_template("tsukkomi_index.html", error="URLを入力してください")
        
        try:
            # 1. 字幕取得
            vtt_path = download_captions(youtube_url)
            if not vtt_path:
                return render_template("tsukkomi_index.html", error="字幕が見つかりませんでした。日本語字幕付きの動画URLか確認してください。")
            
            title = vtt_path.stem.replace(".ja", "").replace(".en", "") # 簡易整形
            raw_lines = parse_vtt(vtt_path)
            cleaned_text = clean_text(raw_lines)

            # --- 文字起こしテキストの保存 ---
            txt_filename = f"{title}.txt"
            txt_path = CAPTIONS_DIR / txt_filename
            with txt_path.open("w", encoding="utf-8") as f:
                f.write(cleaned_text)
            print(f"✅ 文字起こし保存完了: {txt_path}")
            # -----------------------------------
            
            # 2. 分析実行
            analysis_md = analyze_tsukkomi(cleaned_text, title)
            
            # 3. HTML整形
            analysis_html = markdown.markdown(analysis_md, extensions=["tables", "fenced_code"])
            
            return render_template(
                "tsukkomi_result.html",
                title=title,
                video_url=youtube_url,
                analysis_html=analysis_html
            )
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return render_template("tsukkomi_index.html", error=f"エラーが発生しました: {str(e)}")

    return render_template("tsukkomi_index.html")

@app.route("/shutdown", methods=["POST"])
def shutdown():
    func = request.environ.get("werkzeug.server.shutdown")
    if func is None:
        import os, signal
        os.kill(os.getpid(), signal.SIGINT)
        return "Server shutting down..."
    func()
    return "Server shutting down..."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081)) # 8081 to avoid conflict if both run
    app.run(host="0.0.0.0", port=port, debug=True)
