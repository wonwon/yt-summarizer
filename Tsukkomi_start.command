#!/bin/bash
cd "$(dirname "$0")"
dot_clean -m .

# ポート番号設定
export PORT=8081

# ブラウザを開く関数 (並列実行)
(sleep 3 && open "http://localhost:$PORT") &

# Flaskアプリ起動
echo "🚀 Starting Tsukkomi Analyzer on Port $PORT..."
python3 app_tsukkomi.py 2> startup_error.log
