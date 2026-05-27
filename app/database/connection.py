"""
connection.py — Database Connection Manager

Provides a thread-safe PostgreSQL connection pool using psycopg2.
Reads DATABASE_URL from Pydantic settings (loaded from .env).

Pool lifecycle is managed by FastAPI's lifespan (see main.py).

Usage:
    from app.database.connection import get_cursor

    with get_cursor() as cur:
        cur.execute("SELECT * FROM documents")
        rows = cur.fetchall()  # Returns list of RealDictRow (acts like dict)
"""

import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.pool
import psycopg2.extras
from pgvector.psycopg2 import register_vector

logger = logging.getLogger(__name__)

# Module-level pool — initialized by init_pool(), torn down by close_pool()
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def init_pool() -> None:
    """
    Creates the connection pool. Called once at application startup via lifespan.
    Registers pgvector on a test connection to validate the extension is present.
    """
    global _pool
    from app.config import settings

    logger.info("Initializing database connection pool...")
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=10,
        dsn=settings.DATABASE_URL,
    )

    # Validate connection and register vector type on a test conn
    conn = _pool.getconn()
    try:
        register_vector(conn)
        conn.commit()
        logger.info("Database pool initialized. pgvector extension verified.")
    finally:
        _pool.putconn(conn)


def close_pool() -> None:
    """
    Closes all connections in the pool. Called at application shutdown via lifespan.
    """
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("Database connection pool closed.")


@contextmanager
def get_cursor(autocommit: bool = False, max_retries: int = 3) -> Generator:
    """
    Context manager that borrows a connection from the pool and yields a cursor.
    Validates connection health before yielding (important for serverless DBs like Neon).
    Automatically commits on success, rolls back on error, and returns the
    connection to the pool regardless of outcome.
    """
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialized. "
            "Ensure init_pool() is called at application startup."
        )

    conn = None
    # 1. Obtain a healthy connection
    for attempt in range(max_retries):
        conn = _pool.getconn()
        conn.autocommit = autocommit
        try:
            # Ping connection to ensure it wasn't dropped by the server
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            break  # Healthy
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            logger.warning(f"Discarding dead DB connection (attempt {attempt + 1}/{max_retries}): {e}")
            _pool.putconn(conn, close=True)
            conn = None
            if attempt == max_retries - 1:
                raise

    # 2. Yield cursor
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            register_vector(conn)
            yield cur
            if not autocommit:
                conn.commit()
    except Exception:
        if not autocommit:
            try:
                conn.rollback()
            except psycopg2.InterfaceError:
                pass # Connection already closed, nothing to rollback
        raise
    finally:
        # Check if the connection broke during the transaction
        is_broken = False
        try:
            if conn.closed != 0:
                is_broken = True
        except Exception:
            is_broken = True
            
        _pool.putconn(conn, close=is_broken)


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    print("\nTesting database connection pool...\n")
    try:
        init_pool()
        with get_cursor() as cur:
            cur.execute("SELECT version();")
            row = cur.fetchone()
            print(f"Connected! PostgreSQL: {row['version'][:50]}")

            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            result = cur.fetchone()
            if result:
                print("pgvector extension is installed.")
            else:
                print("WARNING: pgvector NOT found. Run schema_unified.sql first.")
        close_pool()
    except Exception as err:
        print(f"Connection failed: {err}")
