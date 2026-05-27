import asyncio
from app.database.connection import init_pool, get_cursor

def update_model():
    init_pool()
    with get_cursor() as cursor:
        cursor.execute("UPDATE system_settings SET value = 'gemini-3.1-flash-lite' WHERE key = 'current_llm_model'")
        print("Updated current_llm_model to gemini-3.1-flash-lite")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    update_model()
