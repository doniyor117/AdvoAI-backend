"""
connection.py — Database Connection Manager

Provides a reusable PostgreSQL connection using psycopg2.
Reads DATABASE_URL from .env so you can swap between local
PostgreSQL and Neon without changing any code.

Usage:
    from app.database.connection import get_connection

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        print(cur.fetchone())
    conn.close()
"""

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extensions import connection as PgConnection
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector

# Load .env file from project root
load_dotenv()


def get_connection() -> PgConnection:
    """
    Creates and returns a new PostgreSQL connection.

    Reads DATABASE_URL from environment variables.
    Registers the pgvector type so embedding columns
    work natively with Python lists.
    """
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "❌ DATABASE_URL not set. "
            "Copy .env.example to .env and fill in your connection string."
        )

    print(f"🔌 Connecting to database...")
    conn = psycopg2.connect(database_url)

    # Register pgvector so we can insert/read vector columns as Python lists
    register_vector(conn)
    # register_vector executes a SELECT, so we must commit the implicit transaction
    conn.commit()

    print("✅ Database connected.")
    return conn


@contextmanager
def get_cursor(autocommit: bool = False) -> Generator:
    """
    Context manager that provides a database cursor.
    Handles connection lifecycle and commits/rollbacks automatically.

    Usage:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM documents")
            rows = cur.fetchall()

    Args:
        autocommit: If True, each statement commits immediately.
    """
    conn = get_connection()
    conn.autocommit = autocommit

    try:
        with conn.cursor() as cur:
            yield cur
            if not autocommit:
                conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🧪 Testing database connection...\n")

    try:
        with get_cursor() as cur:
            cur.execute("SELECT version();")
            version = cur.fetchone()[0]
            print(f"✅ Connected! PostgreSQL version:\n   {version}")

            # Check if pgvector extension is available
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            result = cur.fetchone()
            if result:
                print("✅ pgvector extension is installed.")
            else:
                print("⚠️  pgvector extension NOT found. Run schema.sql first.")

    except Exception as err:
        print(f"❌ Connection failed: {err}")
