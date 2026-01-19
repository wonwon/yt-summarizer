#!/bin/bash
cd "$(dirname "$0")"
dot_clean -m .

# 仮想環境の有効化
if [ -d "$HOME/YouTubeInsightGen_venv" ]; then
    source $HOME/YouTubeInsightGen_venv/bin/activate
else
    echo "❌ 仮想環境が見つかりません: $HOME/YouTubeInsightGen_venv"
    echo "以下のコマンドで作成してください:"
    echo "python3 -m venv ~/YouTubeInsightGen_venv"
    echo "~/YouTubeInsightGen_venv/bin/pip install -r requirements.txt"
    read -p "[Enter] キーを押して終了してください..."
    exit 1
fi

# Geminiモデルを指定
export GEMINI_MODEL="gemini-3-pro-preview"
export PORT=8081
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

echo "ブラウザで http://127.0.0.1:8081 にアクセスしてください。"
echo "⚠ 注意: サーバー実行中は、このターミナルウィンドウを閉じないでください。"

# ブラウザを自動で開く (バックグラウンドで2秒後に実行)
(sleep 2 && open http://127.0.0.1:8081) &

# Flaskアプリの起動
python app.py

# 正常終了かエラーかで分岐
if [ $? -ne 0 ]; then
    echo "❌ アプリケーションがエラーで終了しました。"
    read -p "[Enter] キーを押して終了してください..."
    exit 1
else
    echo "アプリケーションを停止しました。"
    # 正常終了時は少し待ってから閉じる（余韻のため）
    sleep 1
    exit 0
fi
