import os

import google.generativeai as genai


def setup_gemini_model(api_key=None):
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が環境変数または引数で指定されていません。")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-pro")  # または "gemini-2.5-flash"

def generate_text(model, prompt: str, input_text: str) -> str:
    full_prompt = prompt.strip() + "\n\n" + input_text.strip()
    response = model.generate_content(full_prompt)
    return (response.text or "").strip()