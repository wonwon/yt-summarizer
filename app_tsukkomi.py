import os
import re
import subprocess
import json
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import google.generativeai as genai
import markdown
from dotenv import load_dotenv
from flask import Flask, render_template, request

# --- 設定 ---
PORT = int(os.environ.get("PORT", 8081))
MODEL_NAME = "gemini-2.5-flash-lite"
CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "tsukkomi_secret_key"

load_dotenv()

# Gemini APIキー設定
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY_PRIMARY") or os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY が設定されていません")
    import sys
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)

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
    cmd = [
        "yt-dlp",
        "--impersonate", "chrome",
        "--extractor-args", "youtube:player_client=web_creator,ios,android",
        "--write-auto-sub",
        "--sub-lang", "ja,en",
        "--skip-download",
        "--output", str(CAPTIONS_DIR / "%(title)s [%(id)s].%(ext)s"),
        clean_url,
    ]

    if os.path.exists("cookies.txt"):
        cmd.insert(1, "--cookies")
        cmd.insert(2, "cookies.txt")

    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"⚠️ yt-dlp エラー: {e}")
        return None

    # 隠しファイル (._*) を除外
    candidates = [p for p in CAPTIONS_DIR.glob("*.vtt") if not p.name.startswith("._")]
    if not candidates:
        return None

    # 優先順位: ja > en > 他
    for p in candidates:
        if ".ja." in p.name: return p
    for p in candidates:
        if ".en." in p.name: return p
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

def analyze_tsukkomi(text: str, title: str) -> str:
    prompt = f"""
あなたはプロのお笑い評論家であり、言葉遊びの達人です。
YouTube動画「{title}」の文字起こしから、独創的な表現やツッコミを抽出してください。

【抽出・分析基準】
1. 独特な言語センス（造語、比喩、パワーワード）
2. 狂気を感じるほどの妄想トークやボケ
3. 鋭いツッコミや、斜め上の視点からの感想

【出力フォーマット】
Markdown形式で出力してください。
特に「フレーズ」「分類」「なぜ面白いのか（背景・言葉遊びの解説）」を明確にしてください。
テーブル形式を活用すると見やすいです。

--- 文字起こし開始 ---
{text}
--- 文字起こし終了 ---
"""
    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(prompt)
    return response.text

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("youtube_url")
        if not url:
            return render_template("tsukkomi_index.html", error="URLを入力してください")

        # 既存ファイル削除
        for f in CAPTIONS_DIR.glob("*"):
            try: f.unlink()
            except: pass

        vtt_path = download_captions(url)
        if not vtt_path:
            return render_template("tsukkomi_index.html", error="字幕の取得に失敗しました（字幕設定がない、または非公開など）")

        title = vtt_path.stem
        cleaned = clean_text(parse_vtt(vtt_path))
        
        analysis_md = analyze_tsukkomi(cleaned, title)
        analysis_html = markdown.markdown(analysis_md, extensions=["tables", "fenced_code"])
        
        # captionsフォルダーをクリーンアップ
        for file in CAPTIONS_DIR.glob("*"):
            try:
                file.unlink()
            except:
                pass
        
        return render_template(
            "tsukkomi_result.html",
            title=title,
            video_url=clean_youtube_url(url),
            analysis_html=analysis_html
        )

    return render_template("tsukkomi_index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
