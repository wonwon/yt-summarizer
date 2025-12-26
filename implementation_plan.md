# ツッコミ・妄想分析アプリ 実装計画 (Tsukkomi Analyzer)

## 概要

YouTube 動画（特定のチャンネル等）の字幕情報から、激しいツッコミや妄想、独特な言語センスで語られる「ギャグフレーズ」を抽出し、そのおもしろさの構造や「なにとかけているのか（元ネタや言葉遊び）」を分析する専用アプリケーションを構築します。

既存の `app.py` とは分離した `app_tsukkomi.py` として実装し、モデルには `gemini-2.5-flash-lite` を採用します。

## 実装内容

### 1. 新規アプリケーションファイル: `app_tsukkomi.py`

既存の `app.py` の有用なロジック（字幕取得、クリーニング）を流用・調整しつつ、以下の独自機能を実装します。

- **Gemini モデル設定**: `gemini-2.5-flash-lite` を固定で使用。
- **専用プロンプト**:
  - 役割: ツッコミ・お笑い分析の専門家。
  - タスク: 動画内のテキストから「独特なフレーズ」「激しいツッコミ」「妄想トーク」を抽出。
  - 出力: フレーズ、分類（ツッコミ/妄想/ワードセンス）、解説（なにとかけているか、文脈、おもしろさの理由）。
- **ルーティング**:
  - `/`: 動画 URL 入力フォーム。
  - `/analyze`: 分析結果表示。

### 2. フロントエンド (Templates)

分離したアプリとしての体裁を整えるため、専用テンプレートを作成します。

- `templates/tsukkomi_index.html`: URL 入力画面。お笑い分析っぽい独自のスタイル（少しポップなデザイン等）を適用。
- `templates/tsukkomi_result.html`: 分析結果表示画面。抽出されたフレーズと解説をカード形式やリスト形式で見やすく表示。

### 3. 起動スクリプト

- `Tsukkomi_start.command`: Mac ですぐに起動できるシェルスクリプトを作成。
  - 仮想環境 (`venv`) のアクティベート。
  - `python app_tsukkomi.py` の実行。
  - ブラウザの自動起動。

## 技術スタック

- Framework: Flask
- AI Model: Gemini 2.5 Flash Lite
- External Tools: yt-dlp (字幕取得)
- UI: HTML/CSS (Vanilla)

## ファイル構成予定

```
/Users/tanakaseiji/Project/GeminiCLI/YouTubeInsightGen/
  ├── app.py (既存)
  ├── app_tsukkomi.py (新規)
  ├── templates/
  │    ├── tsukkomi_index.html (新規)
  │    └── tsukkomi_result.html (新規)
  └── Tsukkomi_start.command (新規)
```

## プロンプト設計案 (概念)

```text
あなたはプロのお笑い評論家であり、言葉遊びの達人です。
入力されたテキストから、以下の基準で「ギャグフレーズ」や「キレのあるツッコミ」を抽出、分析してください。

抽出基準:
1. 独特な言語センスによる造語や比喩
2. 常軌を逸した妄想トーク
3. 鋭いツッコミ、またはボケ

出力形式（JSONまたはMarkdown表）:
- フレーズ: (抽出した言葉)
- 分類: (ツッコミ/妄想/パワーワード)
- なにとかけているか/元ネタ: (言葉遊びの解説、背景知識)
- 笑いのポイント: (なぜ面白いのかの分析)
```

## 手順

1. `app_tsukkomi.py` の作成。
2. `templates/tsukkomi_index.html`, `templates/tsukkomi_result.html` の作成。
3. `Tsukkomi_start.command` の作成・実行権限付与。
