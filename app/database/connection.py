"""
connection.py — Database Connection Manager

Provides an async PostgreSQL connection pool using asyncpg.
Reads DATABASE_URL from Pydantic settings (loaded from .env).

Pool lifecycle is managed by FastAPI's lifespan (see main.py).

Usage:
    from app.database.connection import get_connection

    async with get_connection() as conn:
        rows = await conn.fetch("SELECT * FROM documents")
        # Returns list of asyncpg.Record (acts like dict)
"""

import logging
from contextlib import asynccontextmanager

import asyncpg
from pgvector.asyncpg import register_vector

logger = logging.getLogger(__name__)

# Module-level pool — initialized by init_pool(), torn down by close_pool()
_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """
    Creates the connection pool asynchronously. Called at application startup via lifespan.
    Registers pgvector on connections to validate the extension is present.
    """
    global _pool
    from app.config import settings

    logger.info("Initializing asyncpg connection pool...")
    
    async def init_connection(conn):
        await register_vector(conn)
        
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        init=init_connection
    )

    logger.info("Database pool initialized. pgvector async extension verified.")


import re

async def close_pool() -> None:
    """
    Closes all connections in the pool. Called at application shutdown via lifespan.
    """
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed.")


def _adapt_sql(sql: str, params=None) -> tuple[str, list]:
    if params is None:
        return sql, []
    
    if isinstance(params, dict):
        args = []
        def repl(m):
            key = m.group(1)
            if key not in params:
                raise KeyError(f"Missing parameter {key}")
            args.append(params[key])
            return f"${len(args)}"
        new_sql = re.sub(r"%\(([^)]+)\)s", repl, sql)
        return new_sql, args
    elif isinstance(params, (list, tuple)):
        args = list(params)
        def repl(m):
            if not hasattr(repl, "count"):
                repl.count = 0
            repl.count += 1
            return f"${repl.count}"
        new_sql = re.sub(r"%s", repl, sql)
        return new_sql, args
    else:
        return sql, [params]


class AsyncCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_result = None

    async def execute(self, sql: str, params=None):
        new_sql, args = _adapt_sql(sql, params)
        self._last_result = await self.conn.fetch(new_sql, *args)
    
    def fetchone(self):
        if self._last_result:
            return dict(self._last_result[0])
        return None
        
    def fetchall(self):
        if self._last_result:
            return [dict(r) for r in self._last_result]
        return []

@asynccontextmanager
async def get_connection():
    """
    Async context manager that borrows a connection from the pool and yields an AsyncCursor wrapper.
    Automatically returns the connection to the pool.
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialized. "
            "Ensure await init_pool() is called at application startup."
        )

    async with _pool.acquire() as conn:
        yield AsyncCursor(conn)

# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()

    async def test():
        print("\nTesting database connection pool...\n")
        try:
            await init_pool()
            async with get_connection() as cur:
                await cur.execute("SELECT version();")
                version = cur.fetchone()["version"]
                print(f"Connected! PostgreSQL: {version[:50]}")

                await cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
                ext = cur.fetchone()
                if ext:
                    print("pgvector extension is installed.")
                else:
                    print("WARNING: pgvector NOT found. Run schema_unified.sql first.")
            await close_pool()
        except Exception as err:
            print(f"Connection failed: {err}")

    asyncio.run(test())
