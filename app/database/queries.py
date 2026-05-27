"""
queries.py — Database Query Functions

All SQL operations using parameterized queries.
Rows returned as dicts via RealDictCursor (configured in connection.py).
"""

import json
import logging
from typing import Dict, List, Any, Optional

from app.database.connection import get_cursor

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────

def _row_to_str(row: dict, *uuid_keys: str) -> dict:
    """Converts uuid fields to str in a RealDictRow (in-place). Returns row."""
    for k in uuid_keys:
        if row.get(k) is not None:
            row[k] = str(row[k])
    return dict(row)


# ── Document Queries ──────────────────────────────────────────

def check_duplicate(source_doc_id: str) -> bool:
    """Returns True if a document with this source_doc_id already exists."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM documents WHERE source_doc_id = %s LIMIT 1;",
            (source_doc_id,)
        )
        return cur.fetchone() is not None


def insert_document(record: Dict[str, Any]) -> None:
    """
    Inserts a parent document into the documents table.
    """
    sql = """
        INSERT INTO documents (
            id, source_doc_id, title, act_type,
            doc_date, source_url, is_active
        )
        VALUES (
            %(id)s, %(source_doc_id)s, %(title)s, %(act_type)s,
            %(doc_date)s, %(source_url)s, %(is_active)s
        );
    """
    with get_cursor() as cur:
        cur.execute(sql, record)
    logger.info(f"Document saved: {record['source_doc_id']} -> {record['id'][:8]}...")


def insert_document_parts(parts: List[Dict[str, Any]]) -> None:
    """
    Inserts mega-chunks into the document_parts table.
    Expected keys: id, document_id, part_title, text, part_index
    """
    if not parts:
        return
        
    sql = """
        INSERT INTO document_parts (id, document_id, part_title, text, part_index)
        VALUES (%(id)s, %(document_id)s, %(part_title)s, %(text)s, %(part_index)s);
    """
    with get_cursor() as cur:
        for part in parts:
            cur.execute(sql, {
                "id": part["id"],
                "document_id": part["document_id"],
                "part_title": part["part_title"],
                "text": part["text"],
                "part_index": part.get("part_index", 0),
            })
    logger.info(f"{len(parts)} document parts saved.")


def insert_chunks(chunks: List[Dict[str, Any]]) -> None:
    """
    Batch-inserts search chunks.
    Expected keys: id, document_part_id, text, embedding, chunk_metadata
    """
    if not chunks:
        logger.warning("insert_chunks called with empty list — nothing to insert.")
        return

    sql = """
        INSERT INTO chunks (id, document_part_id, text, embedding, chunk_metadata)
        VALUES (%(id)s, %(document_part_id)s, %(text)s, %(embedding)s, %(chunk_metadata)s);
    """
    with get_cursor() as cur:
        for chunk in chunks:
            cur.execute(sql, {
                "id": chunk["id"],
                "document_part_id": chunk["document_part_id"],
                "text": chunk["text"],
                "embedding": chunk.get("embedding"),
                "chunk_metadata": json.dumps(chunk.get("chunk_metadata", {})),
            })

    logger.info(f"{len(chunks)} chunks saved to database.")


# ── RAG Retrieval Queries ─────────────────────────────────────

def search_similar_chunks(query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
    """Finds the top-K most similar chunks to the query embedding."""
    sql = """
        SELECT
            id,
            document_part_id,
            text,
            1 - (embedding <=> %s::vector) AS similarity
        FROM chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (query_embedding, query_embedding, top_k))
        rows = cur.fetchall()

    return [
        {
            "chunk_id": str(row["id"]),
            "document_part_id": str(row["document_part_id"]),
            "text": row["text"],
            "similarity": round(float(row["similarity"]), 4),
        }
        for row in rows
    ]


def fetch_document_parts(part_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetches full text for the given document_part UUIDs, joined with document metadata."""
    if not part_ids:
        return []

    sql = """
        SELECT dp.id AS part_id, dp.text AS full_markdown, dp.part_title AS title, 
               d.source_doc_id, d.source_url
        FROM document_parts dp
        JOIN documents d ON dp.document_id = d.id
        WHERE dp.id = ANY(%s::uuid[]);
    """
    with get_cursor() as cur:
        cur.execute(sql, (part_ids,))
        rows = cur.fetchall()

    return [
        {
            "id": str(row["part_id"]),
            "title": row["title"],
            "full_markdown": row["full_markdown"],
            "source_doc_id": row["source_doc_id"],
            "source_url": row["source_url"],
        }
        for row in rows
    ]


# ── User Auth Queries ─────────────────────────────────────────

def _user_row(row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "password_hash": row["password_hash"],
        "full_name": row["full_name"],
        "role": row["role"],
        "auth_provider": row["auth_provider"],
        "google_id": row["google_id"],
        "email_verified": row["email_verified"],
        "is_active": row["is_active"],
        "is_banned": row.get("is_banned", False),
        "created_at": row["created_at"],
        "last_login_at": row.get("last_login_at"),
    }


def create_user(
    email: str,
    password_hash: str = None,
    full_name: str = None,
    auth_provider: str = "email",
    google_id: str = None,
    email_verified: bool = False,
) -> Optional[Dict[str, Any]]:
    sql = """
        INSERT INTO users (email, password_hash, full_name, auth_provider, google_id, email_verified)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, email, password_hash, full_name, role, auth_provider,
                  google_id, email_verified, is_active, is_banned, created_at, last_login_at;
    """
    with get_cursor() as cur:
        cur.execute(sql, (email, password_hash, full_name, auth_provider, google_id, email_verified))
        row = cur.fetchone()
    return _user_row(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, is_banned, created_at, last_login_at
        FROM users WHERE email = %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (email,))
        row = cur.fetchone()
    return _user_row(row) if row else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, is_banned, created_at, last_login_at
        FROM users WHERE id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return _user_row(row) if row else None


def get_user_by_google_id(google_id: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, is_banned, created_at, last_login_at
        FROM users WHERE google_id = %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (google_id,))
        row = cur.fetchone()
    return _user_row(row) if row else None


def link_google_id(user_id: str, google_id: str) -> None:
    with get_cursor() as cur:
        cur.execute("UPDATE users SET google_id = %s WHERE id = %s::uuid;", (google_id, user_id))


def update_last_login(user_id: str) -> None:
    with get_cursor() as cur:
        cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s::uuid;", (user_id,))


def update_user_profile(user_id: str, full_name: str) -> None:
    with get_cursor() as cur:
        cur.execute("UPDATE users SET full_name = %s WHERE id = %s::uuid;", (full_name, user_id))


# ── Chat Session & Messages Queries ─────────────────────────

def _session_row(row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "title": row["title"],
        "session_summary": row["session_summary"],
        "is_pinned": row["is_pinned"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def create_session(user_id: str, title: str = "New Chat") -> Optional[Dict[str, Any]]:
    sql = """
        INSERT INTO chat_sessions (user_id, title)
        VALUES (%s::uuid, %s)
        RETURNING id, user_id, title, session_summary, is_pinned, created_at, updated_at;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id, title))
        row = cur.fetchone()
    return _session_row(row) if row else None


def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    sql = """
        SELECT id, user_id, title, session_summary, is_pinned, created_at, updated_at
        FROM chat_sessions
        WHERE user_id = %s::uuid
        ORDER BY is_pinned DESC, updated_at DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        rows = cur.fetchall()
    return [_session_row(r) for r in rows]


def get_session_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, user_id, title, session_summary, is_pinned, created_at, updated_at
        FROM chat_sessions WHERE id = %s::uuid;
    """
    with get_cursor() as cur:
        cur.execute(sql, (session_id,))
        row = cur.fetchone()
    return _session_row(row) if row else None


def update_session_summary(session_id: str, summary: str) -> None:
    sql = "UPDATE chat_sessions SET session_summary = %s, updated_at = NOW() WHERE id = %s::uuid;"
    with get_cursor() as cur:
        cur.execute(sql, (summary, session_id))


def rename_session(session_id: str, title: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE chat_sessions SET title = %s, updated_at = NOW() WHERE id = %s::uuid;",
            (title, session_id)
        )


def toggle_pin_session(session_id: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE chat_sessions SET is_pinned = NOT is_pinned, updated_at = NOW() WHERE id = %s::uuid;",
            (session_id,)
        )


def delete_session(session_id: str) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM chat_sessions WHERE id = %s::uuid;", (session_id,))


def insert_message(session_id: str, role: str, content: str) -> None:
    sql = """
        INSERT INTO chat_messages (session_id, role, content)
        VALUES (%s::uuid, %s, %s);
    """
    with get_cursor() as cur:
        cur.execute(sql, (session_id, role, content))
        # Also touch the session
        cur.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s::uuid;", (session_id,))


def get_session_messages(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetches the last N messages for a session, ordered chronologically."""
    sql = """
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE session_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT %s;
    """
    with get_cursor() as cur:
        cur.execute(sql, (session_id, limit))
        rows = cur.fetchall()
    
    # Reverse to return in chronological order
    result = []
    for row in reversed(rows):
        result.append({
            "id": str(row["id"]),
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"].isoformat()
        })
    return result


def delete_oldest_message(session_id: str) -> Optional[Dict[str, Any]]:
    """Deletes and returns the oldest message in the session, useful for archiving shift."""
    sql = """
        DELETE FROM chat_messages 
        WHERE id = (
            SELECT id FROM chat_messages 
            WHERE session_id = %s::uuid 
            ORDER BY created_at ASC 
            LIMIT 1
        )
        RETURNING id, role, content, created_at;
    """
    with get_cursor() as cur:
        cur.execute(sql, (session_id,))
        row = cur.fetchone()
    
    if row:
        return {
            "id": str(row["id"]),
            "role": row["role"],
            "content": row["content"],
        }
    return None


# ── Usage Tracking Queries ────────────────────────────────────

def get_daily_usage(user_id: str) -> int:
    with get_cursor() as cur:
        cur.execute(
            "SELECT message_count FROM usage_logs WHERE user_id = %s::uuid AND usage_date = CURRENT_DATE;",
            (user_id,)
        )
        row = cur.fetchone()
    return row["message_count"] if row else 0


def increment_usage(user_id: str) -> int:
    sql = """
        INSERT INTO usage_logs (user_id, usage_date, message_count)
        VALUES (%s::uuid, CURRENT_DATE, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET message_count = usage_logs.message_count + 1
        RETURNING message_count;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row["message_count"] if row else 1


def get_guest_usage(fingerprint: str) -> int:
    with get_cursor() as cur:
        cur.execute("SELECT message_count FROM guest_usage WHERE fingerprint = %s;", (fingerprint,))
        row = cur.fetchone()
    return row["message_count"] if row else 0


def increment_guest_usage(fingerprint: str) -> int:
    sql = """
        INSERT INTO guest_usage (fingerprint, message_count)
        VALUES (%s, 1)
        ON CONFLICT (fingerprint)
        DO UPDATE SET message_count = guest_usage.message_count + 1,
                      last_seen_at = NOW()
        RETURNING message_count;
    """
    with get_cursor() as cur:
        cur.execute(sql, (fingerprint,))
        row = cur.fetchone()
    return row["message_count"] if row else 1


# ── Admin Queries ─────────────────────────────────────────────

def get_all_users() -> List[Dict[str, Any]]:
    sql = """
        SELECT u.id, u.email, u.full_name, u.role, u.auth_provider,
               u.email_verified, u.is_active, u.created_at, u.last_login_at,
               COALESCE(ul.message_count, 0) AS today_usage,
               COALESCE(u.is_banned, FALSE) AS is_banned
        FROM users u
        LEFT JOIN usage_logs ul ON u.id = ul.user_id AND ul.usage_date = CURRENT_DATE
        ORDER BY u.created_at DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    return [
        {
            "id": str(r["id"]), "email": r["email"], "full_name": r["full_name"],
            "role": r["role"], "auth_provider": r["auth_provider"],
            "email_verified": r["email_verified"], "is_active": r["is_active"],
            "created_at": r["created_at"], "last_login_at": r["last_login_at"],
            "today_usage": r["today_usage"], "is_banned": r["is_banned"],
        }
        for r in rows
    ]


def update_user_role(user_id: str, role: str) -> None:
    if role not in ("guest", "free", "admin"):
        raise ValueError(f"Invalid role: {role}")
    with get_cursor() as cur:
        cur.execute("UPDATE users SET role = %s WHERE id = %s::uuid;", (role, user_id))


def get_admin_stats() -> Dict[str, Any]:
    sql = """
        WITH
            user_count     AS (SELECT COUNT(*) AS n FROM users),
            doc_count      AS (SELECT COUNT(*) AS n FROM documents),
            chunk_count    AS (SELECT COUNT(*) AS n FROM chunks),
            dau            AS (SELECT COUNT(DISTINCT user_id) AS n FROM usage_logs WHERE usage_date = CURRENT_DATE),
            daily_msgs     AS (SELECT COALESCE(SUM(message_count), 0) AS n FROM usage_logs WHERE usage_date = CURRENT_DATE)
        SELECT
            (SELECT n FROM user_count)   AS total_users,
            (SELECT n FROM doc_count)    AS total_documents,
            (SELECT n FROM chunk_count)  AS total_chunks,
            (SELECT n FROM dau)          AS daily_active_users,
            (SELECT n FROM daily_msgs)   AS daily_messages;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()

    return {
        "total_users": row["total_users"],
        "total_documents": row["total_documents"],
        "total_chunks": row["total_chunks"],
        "daily_active_users": row["daily_active_users"],
        "daily_messages": row["daily_messages"],
    }


def get_all_documents_admin() -> List[Dict[str, Any]]:
    sql = """
        SELECT d.id, d.source_doc_id, d.title, d.act_type, d.doc_date,
               d.source_url, d.is_active, d.created_at,
               COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN document_parts dp ON d.id = dp.document_id
        LEFT JOIN chunks c ON dp.id = c.document_part_id
        GROUP BY d.id
        ORDER BY d.created_at DESC;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    return [
        {
            "id": str(r["id"]), "source_doc_id": r["source_doc_id"],
            "title": r["title"], "act_type": r["act_type"],
            "doc_date": r["doc_date"], "source_url": r["source_url"],
            "is_active": r["is_active"], "created_at": r["created_at"],
            "chunk_count": r["chunk_count"],
        }
        for r in rows
    ]


# ── System Settings Queries ──────────────────────────────────

def get_all_settings() -> Dict[str, str]:
    with get_cursor() as cur:
        cur.execute("SELECT key, value FROM system_settings;")
        rows = cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_setting(key: str) -> Optional[str]:
    with get_cursor() as cur:
        cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
        row = cur.fetchone()
    return row["value"] if row else None


def update_setting(key: str, value: str) -> None:
    sql = """
        INSERT INTO system_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """
    with get_cursor() as cur:
        cur.execute(sql, (key, value))


# ── Document Management Queries (Admin) ──────────────────────

def get_document_full(doc_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a document and its parts count."""
    sql = """
        SELECT d.id, d.source_doc_id, d.title, d.act_type, d.doc_date,
               d.source_url, d.is_active, d.created_at,
               COUNT(dp.id) AS parts_count
        FROM documents d
        LEFT JOIN document_parts dp ON d.id = dp.document_id
        WHERE d.id = %s::uuid
        GROUP BY d.id;
    """
    with get_cursor() as cur:
        cur.execute(sql, (doc_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row["id"]), "source_doc_id": row["source_doc_id"],
        "title": row["title"], "act_type": row["act_type"],
        "doc_date": row["doc_date"], "source_url": row["source_url"],
        "is_active": row["is_active"], "parts_count": row["parts_count"],
        "created_at": row["created_at"],
    }


def update_document_title(doc_id: str, title: str) -> None:
    with get_cursor() as cur:
        cur.execute("UPDATE documents SET title = %s WHERE id = %s::uuid;", (title, doc_id))


def delete_document(doc_id: str) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id = %s::uuid;", (doc_id,))


# ── User Moderation Queries ──────────────────────────────────

def toggle_ban_user(user_id: str) -> bool:
    sql = """
        UPDATE users SET is_banned = NOT COALESCE(is_banned, FALSE)
        WHERE id = %s::uuid
        RETURNING is_banned;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row["is_banned"] if row else False


def get_user_stats(user_id: str) -> Dict[str, Any]:
    sql = """
        WITH
            daily   AS (SELECT COALESCE(SUM(message_count), 0) AS n FROM usage_logs
                        WHERE user_id = %s::uuid AND usage_date = CURRENT_DATE),
            weekly  AS (SELECT COALESCE(SUM(message_count), 0) AS n FROM usage_logs
                        WHERE user_id = %s::uuid AND usage_date >= CURRENT_DATE - INTERVAL '7 days'),
            total   AS (SELECT COALESCE(SUM(message_count), 0) AS n FROM usage_logs
                        WHERE user_id = %s::uuid),
            sessions AS (SELECT COUNT(*) AS n FROM chat_sessions WHERE user_id = %s::uuid)
        SELECT
            (SELECT n FROM daily)    AS daily_messages,
            (SELECT n FROM weekly)   AS weekly_messages,
            (SELECT n FROM total)    AS total_messages,
            (SELECT n FROM sessions) AS session_count;
    """
    with get_cursor() as cur:
        cur.execute(sql, (user_id, user_id, user_id, user_id))
        row = cur.fetchone()

    return {
        "daily_messages": row["daily_messages"],
        "weekly_messages": row["weekly_messages"],
        "total_messages": row["total_messages"],
        "session_count": row["session_count"],
    }
