import asyncio
from dotenv import load_dotenv
load_dotenv('.env')
from app.database.connection import init_pool, close_pool
from app.database.queries import get_session_messages_audited, get_all_sessions_audited

async def main():
    await init_pool()
    sessions = await get_all_sessions_audited(1, 0)
    if sessions:
        session_id = sessions[0]["id"]
        print("Session:", session_id)
        msgs = await get_session_messages_audited(session_id)
        print("Messages:", len(msgs))
    await close_pool()

if __name__ == "__main__":
    asyncio.run(main())
