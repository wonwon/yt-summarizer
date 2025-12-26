# utils/secret_cache.py
import os

from dotenv import load_dotenv
from google.cloud import secretmanager

load_dotenv()

def get_secret(key_name, cache_key):
    cached = os.getenv(cache_key)
    if cached:
        return cached

    # GCP Secret Managerから取得（初回のみ）
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/your-project/secrets/{key_name}/versions/latest"
    response = client.access_secret_version(name=secret_path)
    value = response.payload.data.decode("UTF-8")

    # 取得後に.envファイルへ保存（簡易キャッシュ）
    with open(".env", "a") as f:
        f.write(f"\n{cache_key}={value}")
    
    return value
