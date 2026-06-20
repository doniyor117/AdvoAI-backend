import asyncio
from app.database.connection import get_connection, init_pool

async def migrate():
    await init_pool()
    queries = [
        "ALTER TABLE public.usage_logs ADD COLUMN IF NOT EXISTS doc_upload_count integer NOT NULL DEFAULT 0;",
        "ALTER TABLE public.usage_logs ADD COLUMN IF NOT EXISTS image_upload_count integer NOT NULL DEFAULT 0;",
        "ALTER TABLE public.guest_usage ADD COLUMN IF NOT EXISTS doc_upload_count integer NOT NULL DEFAULT 0;",
        "ALTER TABLE public.guest_usage ADD COLUMN IF NOT EXISTS image_upload_count integer NOT NULL DEFAULT 0;",
        """
        INSERT INTO system_settings (key, value) VALUES
            ('free_daily_doc_limit', '10'),
            ('free_daily_image_limit', '10'),
            ('guest_doc_limit', '2'),
            ('guest_image_limit', '2')
        ON CONFLICT (key) DO NOTHING;
        """
    ]
    async with get_connection() as cur:
        for q in queries:
            await cur.execute(q)
    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
