# utils/subtitle.py

import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)

def clean_youtube_url(url: str) -> str:
    """
    URLをクリーン化（パラメータなどを除去）
    """
    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)
    video_id = query.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

def get_video_id(url: str) -> str:
    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)
    return query.get("v", [""])[0]

def get_subtitle(youtube_url: str) -> Path | None:
    """
    キャッシュ機能付き字幕取得関数（日本語→英語の順で試行）
    """
    clean_url = clean_youtube_url(youtube_url)
    video_id = get_video_id(clean_url)

    # キャッシュチェック
    existing = list(CAPTIONS_DIR.glob(f"*[{video_id}]*.vtt"))
    if existing:
        return existing[0]

    # 試す言語の順序（日本語→英語）
    sub_langs = ["ja", "en"]

    for lang in sub_langs:
        try:
            subprocess.run(
                [
                    "yt-dlp",
                    "--write-auto-sub",
                    "--sub-lang",
                    lang,
                    "--skip-download",
                    "--output",
                    str(CAPTIONS_DIR / "%(title)s [%(id)s].%(ext)s"),
                    clean_url,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # 成功したら.vttファイルができているか確認
            vtt_files = list(CAPTIONS_DIR.glob(f"*[{video_id}]*.vtt"))
            if vtt_files:
                return vtt_files[0]
        except subprocess.CalledProcessError:
            continue  # 次の言語へ

    print(f"[ERROR] 字幕取得失敗: {youtube_url}")
    return None