"""
queries.py — Database Query Functions

All SQL operations for the documents and chunks tables.
Uses parameterized queries throughout (no SQL injection risk).

Usage:
    from app.database.queries import check_duplicate, insert_document, insert_chunks
"""

from typing import Dict, List, Any, Optional
import json

from app.database.connection import get_cursor


# ── Document Queries ──────────────────────────────────────────

def check_duplicate(source_doc_id: str) -> bool:
    """
    Checks if a document with this source_doc_id already exists.

    Args:
        source_doc_id: The Lex.uz doc number (e.g. '111189').

    Returns:
        True if the document already exists in the database.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM documents WHERE source_doc_id = %s LIMIT 1;",
            (source_doc_id,)
        )
        return cur.fetchone() is not None


def insert_document(record: Dict[str, Any]) -> None:
    """
    Inserts a parent document into the documents table.

    Args:
        record: Dictionary matching the documents table columns.
                Expected keys: id, source_doc_id, title, act_type,
                doc_date, source_url, is_active, full_markdown
    """
    sql = """
        INSERT INTO documents (
            id, source_doc_id, title, act_type,
            doc_date, source_url, is_active, full_markdown
        )
        VALUES (
            %(id)s, %(source_doc_id)s, %(title)s, %(act_type)s,
            %(doc_date)s, %(source_url)s, %(is_active)s, %(full_markdown)s
        );
    """

    with get_cursor() as cur:
        cur.execute(sql, record)

    print(f"💾 Document saved: {record['source_doc_id']} → {record['id'][:8]}...")


def insert_chunks(chunks: List[Dict[str, Any]]) -> None:
    """
    Batch-inserts search chunks into the chunks table.
    Each chunk must already have an 'embedding' field (list of 1024 floats).

    Args:
        chunks: List of chunk dictionaries from the ingestion pipeline.
                Expected keys: id, parent_id, text, embedding, chunk_metadata
    """
    if not chunks:
        print("⚠️  No chunks to insert.")
        return

    sql = """
        INSERT INTO chunks (id, parent_id, text, embedding, chunk_metadata)
        VALUES (%(id)s, %(parent_id)s, %(text)s, %(embedding)s, %(chunk_metadata)s);
    """

    with get_cursor() as cur:
        for chunk in chunks:
            # psycopg2 + pgvector handles list → vector conversion automatically
            cur.execute(sql, {
                "id": chunk["id"],
                "parent_id": chunk["parent_id"],
                "text": chunk["text"],
                "embedding": chunk.get("embedding"),
                "chunk_metadata": json.dumps(chunk.get("chunk_metadata", {})),
            })

    print(f"💾 {len(chunks)} chunks saved to database.")


# ── RAG Retrieval Queries ─────────────────────────────────────

def search_similar_chunks(query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Finds the top-K most similar chunks to the query embedding.
    """
    sql = """
        SELECT
            id,
            parent_id,
            text,
            1 - (embedding <=> %s::vector) AS similarity
        FROM chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """

    with get_cursor() as cur:
        cur.execute(sql, (query_embedding, query_embedding, top_k))
        rows = cur.fetchall()

    results = []
    for row in rows:
        results.append({
            "chunk_id": str(row[0]),
            "parent_id": str(row[1]),
            "text": row[2],
            "similarity": round(float(row[3]), 4),
        })

    return results

def fetch_parent_documents(parent_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Fetches the full Markdown documents for the given parent IDs.
    """
    if not parent_ids:
        return []

    placeholders = ", ".join(["%s"] * len(parent_ids))
    sql = f"""
        SELECT id, title, full_markdown, source_doc_id, source_url
        FROM documents
        WHERE id::text IN ({placeholders});
    """

    with get_cursor() as cur:
        cur.execute(sql, parent_ids)
        rows = cur.fetchall()

    documents = []
    for row in rows:
        documents.append({
            "id": str(row[0]),
            "title": row[1],
            "full_markdown": row[2],
            "source_doc_id": row[3],
            "source_url": row[4],
        })

    return documents


# ── User Auth Queries ─────────────────────────────────────────

def create_user(
    email: str,
    password_hash: str = None,
    full_name: str = None,
    auth_provider: str = "email",
    google_id: str = None,
    email_verified: bool = False,
) -> Optional[Dict[str, Any]]:
    """Creates a new user and returns the user record."""
    sql = """
        INSERT INTO users (email, password_hash, full_name, auth_provider, google_id, email_verified)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, email, password_hash, full_name, role, auth_provider, google_id, email_verified, is_active, created_at;
    """
    with get_cursor() as cur:
        cur.execute(sql, (email, password_hash, full_name, auth_provider, google_id, email_verified))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "email": row[1], "password_hash": row[2],
        "full_name": row[3], "role": row[4], "auth_provider": row[5],
        "google_id": row[6], "email_verified": row[7], "is_active": row[8],
        "created_at": row[9],
    }


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Fetches a user by email address."""
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, created_at, last_login_at
        FROM users WHERE email = %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (email,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "email": row[1], "password_hash": row[2],
        "full_name": row[3], "role": row[4], "auth_provider": row[5],
        "google_id": row[6], "email_verified": row[7], "is_active": row[8],
        "created_at": row[9], "last_login_at": row[10],
    }


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a user by UUID."""
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, created_at, last_login_at
        FROM users WHERE id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "email": row[1], "password_hash": row[2],
        "full_name": row[3], "role": row[4], "auth_provider": row[5],
        "google_id": row[6], "email_verified": row[7], "is_active": row[8],
        "created_at": row[9], "last_login_at": row[10],
    }


def get_user_by_google_id(google_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a user by Google OAuth ID."""
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, created_at, last_login_at
        FROM users WHERE google_id = %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (google_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "email": row[1], "password_hash": row[2],
        "full_name": row[3], "role": row[4], "auth_provider": row[5],
        "google_id": row[6], "email_verified": row[7], "is_active": row[8],
        "created_at": row[9], "last_login_at": row[10],
    }


def link_google_id(user_id: str, google_id: str) -> None:
    """Links a Google ID to an existing user account."""
    sql = "UPDATE users SET google_id = %s WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (google_id, user_id))


def update_last_login(user_id: str) -> None:
    """Updates the last_login_at timestamp."""
    sql = "UPDATE users SET last_login_at = NOW() WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))


def update_user_profile(user_id: str, full_name: str) -> None:
    """Updates a user's profile information."""
    sql = "UPDATE users SET full_name = %s WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (full_name, user_id))


# ── Chat Session Queries ─────────────────────────────────────

def create_session(user_id: str, title: str = "New Chat") -> Optional[Dict[str, Any]]:
    """Creates a new chat session for a user."""
    sql = """
        INSERT INTO chat_sessions (user_id, title)
        VALUES (%s::uuid, %s)
        RETURNING id, user_id, title, rolling_summary, is_pinned, created_at, updated_at;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id, title))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "user_id": str(row[1]), "title": row[2],
        "rolling_summary": row[3], "is_pinned": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
        "updated_at": row[6].isoformat() if row[6] else None,
    }


def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    """Fetches all sessions for a user, ordered by pinned first then most recent."""
    sql = """
        SELECT id, user_id, title, rolling_summary, is_pinned, created_at, updated_at
        FROM chat_sessions
        WHERE user_id = %s::uuid
        ORDER BY is_pinned DESC, updated_at DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        rows = cur.fetchall()

    return [{
        "id": str(r[0]), "user_id": str(r[1]), "title": r[2],
        "rolling_summary": r[3], "is_pinned": r[4],
        "created_at": r[5].isoformat() if r[5] else None,
        "updated_at": r[6].isoformat() if r[6] else None,
    } for r in rows]


def get_session_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a single session by ID."""
    sql = """
        SELECT id, user_id, title, rolling_summary, is_pinned, created_at, updated_at
        FROM chat_sessions WHERE id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (session_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "user_id": str(row[1]), "title": row[2],
        "rolling_summary": row[3], "is_pinned": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
        "updated_at": row[6].isoformat() if row[6] else None,
    }


def update_session_summary(session_id: str, summary: str) -> None:
    """Updates the rolling summary for a session."""
    sql = """
        UPDATE chat_sessions
        SET rolling_summary = %s, updated_at = NOW()
        WHERE id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (summary, session_id))


def rename_session(session_id: str, title: str) -> None:
    """Renames a chat session."""
    sql = "UPDATE chat_sessions SET title = %s, updated_at = NOW() WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (title, session_id))


def toggle_pin_session(session_id: str) -> None:
    """Toggles the pinned status of a session."""
    sql = "UPDATE chat_sessions SET is_pinned = NOT is_pinned, updated_at = NOW() WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (session_id,))


def delete_session(session_id: str) -> None:
    """Deletes a chat session."""
    sql = "DELETE FROM chat_sessions WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (session_id,))


# ── Usage Tracking Queries ────────────────────────────────────

def get_daily_usage(user_id: str) -> int:
    """Gets the message count for today for a registered user."""
    sql = """
        SELECT message_count FROM usage_logs
        WHERE user_id = %s::uuid AND usage_date = CURRENT_DATE;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row[0] if row else 0


def increment_usage(user_id: str) -> None:
    """Atomically increments today's message count for a registered user."""
    sql = """
        INSERT INTO usage_logs (user_id, usage_date, message_count)
        VALUES (%s::uuid, CURRENT_DATE, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET message_count = usage_logs.message_count + 1;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))


def get_guest_usage(fingerprint: str) -> int:
    """Gets the lifetime message count for a guest fingerprint."""
    sql = "SELECT message_count FROM guest_usage WHERE fingerprint = %s;"
    with get_cursor() as cur:
        cur.execute(sql, (fingerprint,))
        row = cur.fetchone()
    return row[0] if row else 0


def increment_guest_usage(fingerprint: str) -> None:
    """Atomically increments the lifetime message count for a guest."""
    sql = """
        INSERT INTO guest_usage (fingerprint, message_count)
        VALUES (%s, 1)
        ON CONFLICT (fingerprint)
        DO UPDATE SET message_count = guest_usage.message_count + 1,
                      last_seen_at = NOW();
    """
    with get_cursor() as cur:
        cur.execute(sql, (fingerprint,))


# ── Admin Queries ─────────────────────────────────────────────

def get_all_users() -> List[Dict[str, Any]]:
    """Fetches all users for the admin panel."""
    sql = """
        SELECT u.id, u.email, u.full_name, u.role, u.auth_provider,
               u.email_verified, u.is_active, u.created_at, u.last_login_at,
               COALESCE(ul.message_count, 0) as today_usage,
               COALESCE(u.is_banned, FALSE) as is_banned
        FROM users u
        LEFT JOIN usage_logs ul ON u.id = ul.user_id AND ul.usage_date = CURRENT_DATE
        ORDER BY u.created_at DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    return [{
        "id": str(r[0]), "email": r[1], "full_name": r[2], "role": r[3],
        "auth_provider": r[4], "email_verified": r[5], "is_active": r[6],
        "created_at": r[7], "last_login_at": r[8], "today_usage": r[9],
        "is_banned": r[10],
    } for r in rows]


def update_user_role(user_id: str, role: str) -> None:
    """Updates a user's role (admin operation)."""
    if role not in ("guest", "free", "admin"):
        raise ValueError(f"Invalid role: {role}")
    sql = "UPDATE users SET role = %s WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (role, user_id))


def get_admin_stats() -> Dict[str, Any]:
    """Aggregated stats for the admin dashboard."""
    stats = {}
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users;")
        stats["total_users"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM documents;")
        stats["total_documents"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM chunks;")
        stats["total_chunks"] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT user_id) FROM usage_logs
            WHERE usage_date = CURRENT_DATE;
        """)
        stats["daily_active_users"] = cur.fetchone()[0]

        cur.execute("""
            SELECT COALESCE(SUM(message_count), 0) FROM usage_logs
            WHERE usage_date = CURRENT_DATE;
        """)
        stats["daily_messages"] = cur.fetchone()[0]

    return stats


def get_all_documents_admin() -> List[Dict[str, Any]]:
    """Fetches all documents for the admin panel (without full_markdown)."""
    sql = """
        SELECT d.id, d.source_doc_id, d.title, d.act_type, d.doc_date,
               d.source_url, d.is_active, d.created_at,
               COUNT(c.id) as chunk_count
        FROM documents d
        LEFT JOIN chunks c ON d.id = c.parent_id
        GROUP BY d.id
        ORDER BY d.created_at DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    return [{
        "id": str(r[0]), "source_doc_id": r[1], "title": r[2],
        "act_type": r[3], "doc_date": r[4], "source_url": r[5],
        "is_active": r[6], "created_at": r[7], "chunk_count": r[8],
    } for r in rows]


# ── System Settings Queries ──────────────────────────────────

def get_all_settings() -> Dict[str, str]:
    """Returns all system settings as a key-value dict."""
    sql = "SELECT key, value FROM system_settings;"
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


def get_setting(key: str) -> Optional[str]:
    """Gets a single setting value by key."""
    sql = "SELECT value FROM system_settings WHERE key = %s;"
    with get_cursor() as cur:
        cur.execute(sql, (key,))
        row = cur.fetchone()
    return row[0] if row else None


def update_setting(key: str, value: str) -> None:
    """Updates a single setting. Creates it if it doesn't exist."""
    sql = """
        INSERT INTO system_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """
    with get_cursor() as cur:
        cur.execute(sql, (key, value))


# ── Document Management Queries (Admin) ──────────────────────

def get_document_full(doc_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a document including its full_markdown content."""
    sql = """
        SELECT id, source_doc_id, title, act_type, doc_date,
               source_url, is_active, full_markdown, created_at
        FROM documents WHERE id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (doc_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]), "source_doc_id": row[1], "title": row[2],
        "act_type": row[3], "doc_date": row[4], "source_url": row[5],
        "is_active": row[6], "full_markdown": row[7], "created_at": row[8],
    }


def update_document_title(doc_id: str, title: str) -> None:
    """Updates a document's title."""
    sql = "UPDATE documents SET title = %s WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (title, doc_id))


def delete_document(doc_id: str) -> None:
    """Deletes a document. ON DELETE CASCADE removes all child chunks."""
    sql = "DELETE FROM documents WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (doc_id,))


# ── User Moderation Queries ──────────────────────────────────

def toggle_ban_user(user_id: str) -> bool:
    """Toggles is_banned status. Returns new is_banned value."""
    sql = """
        UPDATE users SET is_banned = NOT COALESCE(is_banned, FALSE)
        WHERE id = %s::uuid
        RETURNING is_banned;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row[0] if row else False


def get_user_stats(user_id: str) -> Dict[str, Any]:
    """Gets usage statistics for a specific user."""
    stats: Dict[str, Any] = {}
    with get_cursor() as cur:
        # Today
        cur.execute("""
            SELECT COALESCE(message_count, 0) FROM usage_logs
            WHERE user_id = %s::uuid AND usage_date = CURRENT_DATE;
        """, (user_id,))
        row = cur.fetchone()
        stats["daily_messages"] = row[0] if row else 0

        # This week
        cur.execute("""
            SELECT COALESCE(SUM(message_count), 0) FROM usage_logs
            WHERE user_id = %s::uuid AND usage_date >= CURRENT_DATE - INTERVAL '7 days';
        """, (user_id,))
        row = cur.fetchone()
        stats["weekly_messages"] = row[0] if row else 0

        # All time
        cur.execute("""
            SELECT COALESCE(SUM(message_count), 0) FROM usage_logs
            WHERE user_id = %s::uuid;
        """, (user_id,))
        row = cur.fetchone()
        stats["total_messages"] = row[0] if row else 0

        # Session count
        cur.execute("""
            SELECT COUNT(*) FROM chat_sessions WHERE user_id = %s::uuid;
        """, (user_id,))
        row = cur.fetchone()
        stats["session_count"] = row[0] if row else 0

    return stats

