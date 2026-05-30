import asyncio
from dotenv import load_dotenv
load_dotenv('.env')
from app.database.connection import get_connection, init_pool, close_pool

async def main():
    await init_pool()
    async with get_connection() as conn:
        try:
            await conn.execute("ALTER TABLE router_analytics ADD COLUMN IF NOT EXISTS router_time_ms INTEGER;")
            await conn.execute("ALTER TABLE router_analytics ADD COLUMN IF NOT EXISTS llm_time_ms INTEGER;")
            print("Columns added.")
        except Exception as e:
            print("Error:", e)
    await close_pool()

asyncio.run(main())
