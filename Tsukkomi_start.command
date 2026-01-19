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

# ポート番号設定
export PORT=8081

# ブラウザを開く関数 (並列実行)
(sleep 3 && open "http://localhost:$PORT") &

# Flaskアプリ起動
echo "🚀 Starting Tsukkomi Analyzer on Port $PORT..."
python app_tsukkomi.py 2> startup_error.log
