import re
import subprocess
import sys

from youtube_transcript_api import (NoTranscriptFound, TranscriptsDisabled,
                                    YouTubeTranscriptApi)


# YouTube URLから動画IDを抽出
def extract_video_id(url: str) -> str:
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else url.strip()

# 字幕取得
def fetch_transcript(video_id: str, languages=['ja', 'en']) -> str:
    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = transcripts.find_transcript(languages).fetch()
        return "\n".join([item['text'] for item in transcript])
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"⚠️ 字幕取得失敗: {e}")
        return ""
    except Exception as e:
        print(f"⚠️ その他のエラー: {e}")
        return ""

# Gemini CLI へ要約依頼
def summarize_with_gemini(text: str) -> str:
    try:
        # `echo テキスト | gcli` でGemini CLIへパイプ送信
        result = subprocess.run(
            ['gcli', '--model', 'gemini-1.5-pro-latest', '--system', '以下のYouTube字幕を要約してください'],
            input=text.encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return result.stdout.decode('utf-8')
    except Exception as e:
        print(f"⚠️ Gemini CLI 実行エラー: {e}")
        return ""

# メイン処理
def main():
    if len(sys.argv) < 2:
        print("使い方: python analyze_youtube.py <YouTubeのURLまたは動画ID>")
        return

    url_or_id = sys.argv[1]
    video_id = extract_video_id(url_or_id)
    print(f"🎬 動画ID: {video_id}")

    transcript_text = fetch_transcript(video_id)
    if not transcript_text:
        print("⚠️ 字幕が取得できませんでした。")
        return

    print("📄 字幕取得成功。Geminiに要約を依頼します...")
    summary = summarize_with_gemini(transcript_text)
    print("\n🧠 Gemini 要約結果:\n")
    print(summary)

if __name__ == "__main__":
    main()