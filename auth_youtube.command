#!/bin/bash
cd "$(dirname "$0")"

# 仮想環境の存在確認とactivate
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "venvが見つかりません。作成してください。"
    read -p "[Enter] キーを押して終了してください..."
    exit 1
fi

echo "========================================================"
echo "YouTube認証セットアップ (初回のみ)"
echo "========================================================"
echo "この後、画面に「認証コード」と「URL」が表示されます。"
echo "1. URL (google.com/device) をブラウザで開く"
echo "2. 表示されたコードを入力する"
echo "3. Googleアカウントでログインして許可する"
echo "========================================================"
echo ""

# ダミー動画で認証フローを実行 (ダウンロードはしない)
yt-dlp --username oauth2 --password '' --simulate "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

echo ""
echo "========================================================"
echo "認証が完了しました。"
echo "このウィンドウを閉じて、アプリを起動してください。"
echo "========================================================"
read -p "[Enter] キーを押して終了してください..."
