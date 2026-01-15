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

echo "========================================================"
echo "YouTube Cookies セットアップ"
echo "========================================================"
echo "これからChromeブラウザが一時的に起動し、セキュリティの許可を求められます。"
echo "ポップアップが表示されたら「許可（Allow）」または「常に許可」を選択してください。"
echo "これにより 'cookies.txt' が生成され、以降はポップアップが出なくなります。"
echo "========================================================"
read -p "準備ができたら [Enter] キーを押してください..."

# Cookiesをダンプするための一時実行
# --skip-download で動画はダウンロードせず、cookiesだけ処理させる
yt-dlp --impersonate chrome --extractor-args "youtube:player_client=web_creator,ios,android" --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

echo ""
if [ -f "cookies.txt" ]; then
    echo "✅ cookies.txt の生成に成功しました！"
    echo "これでアプリが正常に動作します。"
else
    echo "⚠️ cookies.txt が生成されませんでした。"
    echo "ChromeでYouTubeにログインしているか確認してください。"
    echo "または、拡張機能 'Get cookies.txt LOCALLY' を使用して手動で作成してください。"
fi

echo "========================================================"
read -p "[Enter] キーを押して終了してください..."
