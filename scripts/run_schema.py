import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL not found in .env")
    exit(1)

script_path = os.path.join(os.path.dirname(__file__), "..", "app", "database", "schema_unified.sql")

print("🔌 Connecting to pg with psql and parsing unified schema...")
result = subprocess.run(["psql", DATABASE_URL, "-f", script_path], capture_output=True, text=True)

if result.returncode == 0:
    print("✅ Schema applied successfully!")
    print(result.stdout)
else:
    print("❌ ERROR applying schema:")
    print(result.stderr)
