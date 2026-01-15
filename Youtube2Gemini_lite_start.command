#!/bin/bash
cd "$(dirname "$0")"
dot_clean -m . 
dot_clean -m /Users/tanakaseiji/YouTubeInsightGen_venv

# 仮想環境の存在確認とactivate
if [ -d "/Users/tanakaseiji/YouTubeInsightGen_venv" ]; then
    source /Users/tanakaseiji/YouTubeInsightGen_venv/bin/activate
else
    echo "venvが見つかりません。作成してください。"
    read -p "[Enter] キーを押して終了してください..."
    exit 1
fi

# GeminiモデルをLiteに指定
export GEMINI_MODEL="gemini-2.5-flash-lite"
export PORT=8082
echo "Starting YouTube Insight Gen with Model: $GEMINI_MODEL on Port: $PORT..."

# ポート使用状況を確認し、使用中ならプロセスをkill
echo "Checking port $PORT..."
PID=$(lsof -ti :$PORT)
if [ -n "$PID" ]; then
  echo "Port $PORT is already in use by PID: $PID. Killing process..."
  kill -9 $PID
  echo "Process killed."
else
  echo "Port $PORT is free."
fi

echo "ブラウザで http://127.0.0.1:$PORT にアクセスしてください。"
echo "⚠ 注意: サーバー実行中は、このターミナルウィンドウを閉じないでください。"

# ブラウザを自動で開く (バックグラウンドで2秒後に実行)
(sleep 2 && open http://127.0.0.1:$PORT) &

# Flaskアプリの起動
python app.py

# 正常終了かエラーかで分岐
if [ $? -ne 0 ]; then
    echo "❌ アプリケーションがエラーで終了しました。"
    read -p "[Enter] キーを押して終了してください..."
    exit 1
else
    echo "アプリケーションを停止しました。"
    sleep 1
    exit 0
fi
