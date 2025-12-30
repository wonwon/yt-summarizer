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
echo "YouTube認証セットアップ (重要)"
echo "========================================================"
echo "YouTubeの仕様変更により、従来のOAuth方式は利用できなくなりました。"
echo "現在は Chrome ブラウザから 'cookies.txt' を取得する方法を推奨しています。"
echo ""
echo "これより 'setup_cookies.command' を実行します。"
echo "画面の指示に従い、Chromeブラウザでの許可を行ってください。"
echo "========================================================"
echo ""

# setup_cookies.command を実行
chmod +x setup_cookies.command
./setup_cookies.command

echo ""
echo "========================================================"
echo "処理が完了しました。"
echo "cookies.txt が正常に生成されていれば、準備完了です。"
echo "このウィンドウを閉じて、アプリを起動してください。"
echo "========================================================"
read -p "[Enter] キーを押して終了してください..."
