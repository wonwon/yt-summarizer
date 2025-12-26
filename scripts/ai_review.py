import os
import sys
import argparse
import subprocess
import google.generativeai as genai
from dotenv import load_dotenv

# .env ファイルの読み込み（ローカル実行用）
load_dotenv()

# 設定
DEFAULT_MODEL = "gemini-2.0-flash-exp"
KNOWLEDGE_BASE_FILES = [
    "README.md",
    "development_standard.md",
    ".cursorrules",
    "CONTRIBUTING.md",
    "docs/architecture.md"
]

def run_repomix():
    """repomix を実行してコードをバンドルする"""
    print("📦 1/4: コードの梱包を開始します...")
    try:
        # npx repomix を実行（repomix.config.json がない場合はデフォルト設定で動作）
        subprocess.run(["npx", "repomix", "--style", "markdown", "--output", "repomix-output.md"], check=True)
        print("✅ コードの梱包が完了しました: repomix-output.md")
    except subprocess.CalledProcessError as e:
        print(f"❌ repomix の実行に失敗しました: {e}")
        sys.exit(1)

def get_bundle_content():
    """バンドルされたファイルの内容を読み込む"""
    output_path = "repomix-output.md"
    if not os.path.exists(output_path):
        print(f"❌ バンドルファイルが見つかりません: {output_path}")
        sys.exit(1)
    with open(output_path, "r", encoding="utf-8") as f:
        return f.read()

def collect_knowledge_base():
    """リポジトリ内の主要なドキュメントを収集してナレッジベースを作成する"""
    print("📚 2/4: ナレッジベースを収集しています...")
    knowledge = []
    for filename in KNOWLEDGE_BASE_FILES:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()
                knowledge.append(f"### File: {filename}\n\n{content}")
    
    if not knowledge:
        return "プロジェクト固有のドキュメントは見つかりませんでした。"
    return "\n\n---\n\n".join(knowledge)

def get_prompt_template(review_type):
    """レビュー項目に応じたプロンプトテンプレートを読み込む"""
    prompt_path = f"scripts/prompts/{review_type}.md"
    if not os.path.exists(prompt_path):
        # 汎用的なプロンプトのフォールバック
        prompts = {
            "vulnerability": "セキュリティ脆弱性、不適切なデータ処理、認証・認可の不備を指摘してください。",
            "performance": "効率の悪い処理、N+1問題、メモリ使用量の改善点を指摘してください。",
            "design": "設計、可読性、保守性、命名規則の改善点を指摘してください。"
        }
        return prompts.get(review_type, "コードの問題点を指摘してください。")
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()

def run_ai_review(review_type, bundle_content, knowledge_base):
    """Gemini API を呼び出してレビューを実行する"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY が設定されていません。")
        sys.exit(1)

    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    model = genai.GenerativeModel(model_name)
    
    prompt = get_prompt_template(review_type)
    
    full_prompt = f"""
あなたは世界最高のエンジニアであり、卓越したコードレビューアーです。
提供されたリポジトリ全体のコンテキスト（ソースコードおよびナレッジベース）を深く分析し、
指定された観点で「付加価値の高い」レビューを行ってください。

【リポジトリのナレッジベース（規約・ドキュメント）】
{knowledge_base}

【レビュー観点】
{prompt}

【梱包されたソースコード】
{bundle_content}
"""

    print(f"🔍 3/4: {review_type} レビューを実行中... (Model: {model_name})")
    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        print(f"❌ API呼び出し中にエラーが発生しました: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="汎用型 AI コードレビュー・エンジン")
    parser.add_argument("--type", choices=["vulnerability", "performance", "design"], help="レビューの種類を選択")
    parser.add_argument("--all", action="store_true", help="すべての観点でレビューを実行")
    parser.add_argument("--output", default="ai-review-report.md", help="出力ファイル名")
    
    args = parser.parse_args()
    
    if not args.type and not args.all:
        parser.print_help()
        sys.exit(1)

    # 1. コードの梱包
    run_repomix()
    bundle_content = get_bundle_content()
    
    # 2. ナレッジベースの収集
    knowledge_base = collect_knowledge_base()
    
    # 3. レビュー実行
    review_types = ["vulnerability", "performance", "design"] if args.all else [args.type]
    results = []
    for r_type in review_types:
        result = run_ai_review(r_type, bundle_content, knowledge_base)
        if result:
            results.append(f"# {r_type.upper()} REVIEW RESULTS\n\n{result}")
    
    # 4. 結果の出力
    final_output = "\n\n" + "-"*30 + "\n\n".join(results)
    
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("# 🤖 AI Code Review Comprehensive Report\n\n")
        f.write(f"> **Execution Mode:** {'Full Scan' if args.all else args.type}\n")
        f.write(f"> **System:** Universal Code Review Engine v2\n\n")
        f.write(final_output)
    
    print(f"📄 4/4: レビューレポートを保存しました: {args.output}")

if __name__ == "__main__":
    main()
