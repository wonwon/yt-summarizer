# src/fetch_caption.py
from pytube import YouTube


def fetch_caption(video_url, lang_code='a.ja'):
    yt = YouTube(video_url)
    caption = yt.captions.get_by_language_code(lang_code)
    if not caption:
        raise Exception("字幕が見つかりません")
    return caption.generate_srt_captions()

if __name__ == "__main__":
    video_url = "https://www.youtube.com/watch?v=QyCxLU3EHmo"
    print("🎬 処理中:", video_url)

    try:
        srt_text = fetch_caption(video_url)
        with open("captions_ja.srt", "w", encoding="utf-8") as f:
            f.write(srt_text)
        print("✅ 字幕の保存に成功しました（captions_ja.srt）")
    except Exception as e:
        print("⚠️ エラー:", e)