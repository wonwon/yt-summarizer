#!/bin/bash
cd "$(dirname "$0")"
dot_clean -m . 
dot_clean -m /Users/tanakaseiji/YouTubeInsightGen_venv

# 仮想環境のアクティベート
source /Users/tanakaseiji/YouTubeInsightGen_venv/bin/activate

# ポート番号設定
export PORT=8081

# ブラウザを開く関数 (並列実行)
(sleep 3 && open "http://localhost:$PORT") &

# Flaskアプリ起動
echo "🚀 Starting Tsukkomi Analyzer on Port $PORT..."
python app_tsukkomi.py 2> startup_error.log
