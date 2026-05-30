import asyncio
from dotenv import load_dotenv
load_dotenv('.env')
from app.database.connection import get_connection, init_pool, close_pool

async def main():
    await init_pool()
    async with get_connection() as conn:
        try:
            res = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'router_analytics';")
            print([dict(r) for r in res])
        except Exception as e:
            print(e)
    await close_pool()

asyncio.run(main())
