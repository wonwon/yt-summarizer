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
from google.cloud import texttospeech
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- 設定 ---
TTS_VOICE_NAME = "ja-JP-Standard-B"
TEMP_MP3_FILE = "temp_summary_audio.mp3"
TTS_SPEAKING_RATE = 1.8
TOKEN_FILE = "token.json"
# -----------------

app = Flask(__name__)
app.secret_key = "your_secret_key"

load_dotenv()

# Gemini APIキー（フォールバック対応）
GEMINI_API_KEY_PRIMARY = os.getenv("GEMINI_API_KEY_PRIMARY")
GEMINI_API_KEY_FALLBACK = os.getenv("GEMINI_API_KEY_FALLBACK")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # 後方互換性のため

GMAIL_TO = os.getenv("GMAIL_TO")
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# APIキーのバリデーション
if not (GEMINI_API_KEY_PRIMARY or GEMINI_API_KEY):
    print("❌ GEMINI_API_KEY_PRIMARY または GEMINI_API_KEY が設定されていません")
    import sys
    sys.exit(1)
CAPTIONS_DIR = Path("captions")
CAPTIONS_DIR.mkdir(exist_ok=True)

# 起動時クリーンアップ: 前回残ったファイルを削除
# 異常終了やサーバー再起動時に古いファイルが解析されることを防止
print("🧹 [起動時] captionsフォルダをクリーンアップ中...")
for file in CAPTIONS_DIR.glob("*"):
    try:
        file.unlink()
        print(f"  🗑️ 削除: {file.name}")
    except Exception as e:
        print(f"  ⚠️ 削除失敗: {file.name} - {e}")
print("✅ [起動時] クリーンアップ完了\n")

PROMPTS_FILE = "prompts.json"
PROMPTS = {}

def load_prompts():
    global PROMPTS
    if os.path.exists(PROMPTS_FILE):
        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            PROMPTS = json.load(f)
    else:
        # Fallback if file missing
        PROMPTS = {
            "default": {
                "label": "デフォルト",
                "prompt_template": "要約してください:\n{cleaned_text}"
            }
        }

load_prompts()


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
    cmd = [
            "yt-dlp",
            "--extractor-args", "youtube:player_client=web_creator,ios,android",
            "--write-auto-sub",
            "--sub-lang",
            "ja,en",
            "--skip-download",
            "--output",
            str(CAPTIONS_DIR / "%(title)s [%(id)s].%(ext)s"),
            clean_url,
    ]

    # cookies.txtがあればそれを使う
    if os.path.exists("cookies.txt"):
        cmd.insert(1, "--cookies")
        cmd.insert(2, "cookies.txt")

    # yt-dlpは一部の字幕取得に失敗してもエラー1を返すことがあるため、
    # 実行後にファイルが存在するかどうかで判定する。
    try:
        subprocess.run(
            cmd,
            check=False,
            # capture_output=True, # ログ出力のために追加しても良い
        )
    except Exception as e:
        print(f"⚠️ yt-dlp 実行中に致命的なエラーが発生しました: {e}")
        return None

    # 優先順位: ja > en > 他
    # 隠しファイル (._*) を除外
    candidates = [p for p in CAPTIONS_DIR.glob("*.vtt") if not p.name.startswith("._")]
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


def create_prompt(cleaned_text: str, video_title: str, video_url: str, genre: str = "stock_analyst") -> str:
    prompt_data = PROMPTS.get(genre, PROMPTS.get("stock_analyst")) # Default to stock_analyst if genre not found
    if not prompt_data:
         # Fallback just in case
        return f"要約してください: {cleaned_text}"
    
    template = prompt_data.get("prompt_template", "")
    return template.replace("{cleaned_text}", cleaned_text).replace("{video_title}", video_title).replace("{video_url}", video_url)


def call_gemini(prompt: str) -> str:
    """
    Gemini APIを呼び出し、エラー時に自動的にフォールバックAPIに切り替える
    """
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    
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


def format_as_html(title: str, md_text: str, video_url: str) -> str:
    body_html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return f"""<html><body><h2>{title}</h2><p><a href="{video_url}" target="_blank">🔗 YouTubeで見る</a></p><div>{body_html}</div></body></html>"""


def detect_genre(cleaned_text: str, video_title: str) -> str:
    """Geminiを使って動画のジャンルを判定する"""
    print("▶ ジャンル自動判定開始")
    
    # 候補リスト作成
    candidates = list(PROMPTS.keys()) # ['stock_analyst', 'general']
    candidates_str = ", ".join(candidates)
    
    prompt = f"""
    以下のYouTube動画のタイトルと冒頭のテキストから、最も適切なカテゴリを判定してください。
    
    カテゴリ候補: {candidates_str}
    
    【タイトル】
    {video_title}
    
    【テキスト冒頭】
    {cleaned_text[:1000]}
    
    回答はカテゴリ名のみを出力してください（余計な説明は不要）。
    もし判断がつかない場合は 'general' と出力してください。
    """
    
    try:
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        detected = response.text.strip().lower()
        
        # 候補に含まれているかチェック
        if detected in candidates:
            print(f"✅ 自動判定結果: {detected}")
            return detected
        
        # 候補にない場合やご判定の場合はgeneral
        for cand in candidates:
            if cand in detected:
                print(f"✅ 自動判定結果(部分一致): {cand}")
                return cand
                
        print(f"⚠️ 自動判定不明確 ({detected}) -> default: general")
        return "general"
            
    except Exception as e:
        print(f"❌ 自動判定エラー: {e} -> default: general")
        return "general"



def send_gmail(subject: str, html_body: str, to_email: str, attachment_path: Optional[str] = None):
    if not os.path.exists(TOKEN_FILE):
        print("⚠️ token.json が見つかりません。メール送信をスキップします。")
        return

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        service = build("gmail", "v1", credentials=creds)
        message = MIMEMultipart()
        message["to"] = to_email
        message["subject"] = subject

        # 本文
        message.attach(MIMEText(html_body, "html"))

        # 添付ファイル
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                file_data = f.read()
                file_name = os.path.basename(attachment_path)

            # 添付ファイルのMIMEタイプを自動判別
            mime_type, _ = mimetypes.guess_type(attachment_path)
            mime_type = mime_type.split("/") if mime_type else ["application", "octet-stream"]

            # 添付ファイルの設定
            attachment = MIMEBase(mime_type[0], mime_type[1])
            attachment.set_payload(file_data)
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{file_name}",
            )
            message.attach(attachment)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body = {"raw": raw}
        service.users().messages().send(userId="me", body=body).execute()
        print("✅ メール送信成功")
    except Exception as e:
        print(f"❌ メール送信失敗: {e}")



@app.route("/", methods=["GET", "POST"])
def index():
    youtube_url = None
    mp3_generated = False
    genre = "auto" # default

    # テンプレートに渡すジャンルリスト (プルダウン用)
    genres_for_template = {k: v["label"] for k, v in PROMPTS.items()}

    if request.method == "POST":
        youtube_url = request.form.get("youtube_url")
        genre = request.form.get("genre", "auto")
    elif request.method == "GET":
        # ブックマークレット対応: URLパラメータから動画URLを取得
        youtube_url = request.args.get("url")

    # Gmail認証チェック
    needs_gmail_auth = not os.path.exists(TOKEN_FILE)

    if not youtube_url:
        return render_template("index.html", error_message="URLが指定されていません" if request.method == "POST" else None, genres=genres_for_template, needs_gmail_auth=needs_gmail_auth)

    # 重複処理防止: セッションで最後に処理したURLを追跡
    # from flask import session
    # last_processed_url = session.get('last_processed_url')
    
    # GETリクエスト（ブックマークレット）の場合、同じURLを繰り返し処理しない
    # FIXME: 一旦無効化 - デバッグ中
    # if request.method == "GET" and youtube_url == last_processed_url:
    #     print(f"⚠️ このURLは既に処理済みです: {youtube_url}")
    #     return render_template("index.html", error_message="このURLは既に処理済みです。新しい動画を選択してください。", genres=genres_for_template, needs_gmail_auth=needs_gmail_auth)

    # デバッグ: 受信したURLを確認
    print(f"\n{'='*50}")
    print(f"📥 受信リクエスト情報:")
    print(f"   メソッド: {request.method}")
    print(f"   受信URL: {youtube_url}")
    print(f"   ジャンル: {genre}")
    print(f"{'='*50}\n")

    try:
        print("\n==============================")
        print(f"✅ 受信URL: {youtube_url}")
        print("==============================")

        cleaned_url = clean_youtube_url(youtube_url)

        # 既存ファイル削除（隠しファイルを含むすべてのファイル）
        print("🧹 captionsフォルダをクリーンアップ中...")
        for file in CAPTIONS_DIR.glob("*"):
            if file.is_file():
                try:
                    file.unlink()
                    print(f"  🗑️ 削除: {file.name}")
                except Exception as e:
                    print(f"  ⚠️ 削除失敗: {file.name} - {e}")
        print("✅ クリーンアップ完了")

        vtt_path = download_captions(cleaned_url)
        
        if vtt_path is None:
            return """<h2>❌ 字幕の取得に失敗しました</h2>
            <p>以下の理由が考えられます：</p>
            <ul>
                <li>動画に字幕が設定されていない</li>
                <li>動画が非公開または削除されている</li>
                <li>yt-dlpによる字幕取得に失敗した</li>
            </ul>
            <p><a href="/">戻る</a></p>""", 500
        
        title = vtt_path.stem
        cleaned = clean_text(parse_vtt(vtt_path))

        # テキスト保存
        txt_path = CAPTIONS_DIR / f"{title}.txt"
        with txt_path.open("w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"✅ 字幕テキスト保存: {txt_path}")

        if genre == "auto":
            genre = detect_genre(cleaned, title)

        # Gemini
        prompt = create_prompt(cleaned, title, youtube_url, genre)
        summary_md = call_gemini(prompt)

        if not summary_md:
            return "<h2>❌ Gemini要約取得に失敗しました。</h2>", 500

        # TTS処理
        summary_for_tts = extract_summary_ssml(summary_md)
        if summary_for_tts:
            mp3_generated = generate_gcp_tts_mp3(summary_for_tts, TEMP_MP3_FILE)

        # メール送信
        summary_html = markdown.markdown(summary_md, extensions=["fenced_code", "tables"])
        html_body = format_as_html(title, summary_md, cleaned_url)
        subject = f"【要約・音声完了】{title}"

        attachment_to_send = TEMP_MP3_FILE if mp3_generated and os.path.exists(TEMP_MP3_FILE) else None
        send_gmail(subject, html_body, GMAIL_TO, attachment_to_send)

        # 一時ファイル削除
        if os.path.exists(TEMP_MP3_FILE):
            os.remove(TEMP_MP3_FILE)
            print(f"🗑️ 一時TTSファイル削除: {TEMP_MP3_FILE}")


        # 結果表示（クリーンアップはfinallyブロックで実行）
        escaped_text = cleaned.replace("<", "&lt;").replace(">", "&gt;")
        
        # 処理完了したURLをセッションに記録（重複処理防止用）
        # FIXME: 一旦無効化 - デバッグ中
        # session['last_processed_url'] = youtube_url
        
        return render_template(
            "result.html",
            title=title,
            video_url=cleaned_url,
            text=escaped_text,
            summary_html=summary_html,
            has_audio=bool(attachment_to_send)
        )

    except FileNotFoundError as e:
        return f"<h2>❌ エラー発生</h2><p>字幕ダウンロードに失敗しました。</p><pre>{str(e)}</pre>", 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"<h2>❌ エラー発生</h2><pre>{str(e)}</pre>", 500
    finally:
        # 処理が成功しても失敗しても、必ずcaptionsフォルダーをクリーンアップ
        print("\n🧹 [FINALLY] 字幕ファイルをクリーンアップ中...")
        for file in CAPTIONS_DIR.glob("*"):
            try:
                file.unlink()
                print(f"  🗑️ 削除: {file.name}")
            except Exception as e:
                print(f"  ⚠️ 削除失敗: {file.name} - {e}")
        print("✅ [FINALLY] クリーンアップ完了\n")


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


def extract_summary_ssml(output: str) -> Optional[str]:
    """Gemini出力からTTS用テキストを抽出し、SSMLに整形"""
    print("▶ TTS用SSML抽出開始")
    
    if not output or not output.strip():
        print("⚠️ 出力テキストが空です。SSML生成を中止します。")
        return None

    # フォールバック: 先頭1500文字程度を利用
    # Markdown記号 (#, *) を削除
    text = re.sub(r"[#*]", "", output)     
    text = re.sub(r"`+", "", text)
    text = text.strip()[:1500]
    
    text_cleaned = re.sub(
        r'^[ \t]*[*\-+]\s*|^[ \t]*\d+\.\s*',
        '',
        text,
        flags=re.MULTILINE
    )
    text_cleaned = re.sub(r'\n\s*\n', '\n', text_cleaned).strip()
    ssml_content = text_cleaned.replace('\n', '<break time="120ms"/>')
    ssml = f"<speak>{ssml_content}</speak>"

    print(f"✅ SSML生成完了（プレビュー）:\n{ssml[:200]}...")
    return ssml


def generate_gcp_tts_mp3(text_to_read: str, output_filepath: str) -> bool:
    if not text_to_read:
        print("⚠️ TTS用テキストが空のためスキップ")
        return False

    print(f"▶ Google Cloud TTS 呼び出し開始 (voice={TTS_VOICE_NAME}, rate={TTS_SPEAKING_RATE})")

    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(ssml=text_to_read)
        voice = texttospeech.VoiceSelectionParams(
            language_code="ja-JP",
            name=TTS_VOICE_NAME
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=TTS_SPEAKING_RATE
        )

        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )

        with open(output_filepath, "wb") as out:
            out.write(response.audio_content)

        size = os.path.getsize(output_filepath)
        print(f"✅ TTS音声ファイル生成: {output_filepath} ({size} bytes)")
        return True

    except Exception as e:
        print(f"❌ Google Cloud TTS エラー: {e}")
        return False


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
    import os
    port = int(os.environ.get("PORT", 8080))
    # debug=True はファイル変更時に自動リロードされ、リクエストが重複実行される可能性があるため無効化
    app.run(host="0.0.0.0", port=port, debug=False)

