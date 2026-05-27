import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cursor = conn.cursor()

print("Dropping existing tables...")
cursor.execute("""
DROP TABLE IF EXISTS chunks CASCADE;
DROP TABLE IF EXISTS document_parts CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS chat_sessions CASCADE;
DROP TABLE IF EXISTS usage_logs CASCADE;
DROP TABLE IF EXISTS guest_usage CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS system_settings CASCADE;
""")

print("Running schema_unified.sql...")
with open("app/database/schema_unified.sql", "r") as f:
    sql = f.read()
    
try:
    cursor.execute(sql)
    print("Database initialized successfully with new schema!")
except Exception as e:
    print(f"Error executing schema: {e}")

cursor.close()
conn.close()
