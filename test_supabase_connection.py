
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

# Load the .env file from the project folder.
BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"

load_dotenv(env_file, override=True)

url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

print("=== PyCharm Supabase Connection Test ===")
print("Project folder:", BASE_DIR)
print(".env exists:", env_file.exists())
print("URL loaded:", bool(url))
print("Server key loaded:", bool(key))
print("URL:", url if url else "MISSING")

if not url or not key:
    raise RuntimeError(
        "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing from .env"
    )

client = create_client(url, key)

response = (
    client.table("user_configs")
    .select("user_id,is_active")
    .limit(10)
    .execute()
)

rows = response.data or []

print("\nConnection successful!")
print("Rows returned:", len(rows))
print("Active users:", sum(
    row.get("is_active") is True for row in rows
))

for row in rows:
    print(
        "User ID:", row.get("user_id"),
        "| Active:", row.get("is_active")
    )
