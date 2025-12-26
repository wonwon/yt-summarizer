#!/bin/bash
cd "$(dirname "$0")"

# 仮想環境のアクティベート
source venv/bin/activate

# ポート番号設定
export PORT=8081

# ブラウザを開く関数 (並列実行)
(sleep 3 && open "http://localhost:$PORT") &

# Flaskアプリ起動
echo "🚀 Starting Tsukkomi Analyzer on Port $PORT..."
python app_tsukkomi.py
