"""
queries.py — Database Query Functions

All SQL operations using parameterized queries.
Rows returned as dicts via RealDictCursor (configured in connection.py).
"""
import json
import logging
import re
import time
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

from app.database.connection import get_connection, get_transaction, AsyncCursor

logger = logging.getLogger(__name__)




# ── Helpers ───────────────────────────────────────────────────

def _row_to_str(row: dict, *uuid_keys: str) -> dict:
    """Converts uuid fields to str in a RealDictRow (in-place). Returns row."""
    for k in uuid_keys:
        if row.get(k) is not None:
            row[k] = str(row[k])
    return dict(row)


# ── Document Queries ──────────────────────────────────────────

async def check_duplicate(source_doc_id: str) -> bool:
    """Returns True if a document with this source_doc_id already exists."""
    async with get_connection() as cur:
        await cur.execute(
            "SELECT 1 FROM documents WHERE source_doc_id = %s LIMIT 1;",
            (source_doc_id,)
        )
        return cur.fetchone() is not None


async def insert_document(record: Dict[str, Any]) -> None:
    """
    Inserts a parent document into the documents table.
    """
    sql = """
        INSERT INTO documents (
            id, source_doc_id, title, act_type,
            doc_date, source_url, category, is_active
        )
        VALUES (
            %(id)s, %(source_doc_id)s, %(title)s, %(act_type)s,
            %(doc_date)s, %(source_url)s, %(category)s, %(is_active)s
        );
    """
    async with get_connection() as cur:
        await cur.execute(sql, record)
    logger.info(f"Document saved: {record['source_doc_id']} -> {record['id'][:8]}...")


async def insert_document_parts(parts: List[Dict[str, Any]]) -> None:
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
    async with get_connection() as cur:
        for part in parts:
            await cur.execute(sql, {
                "id": part["id"],
                "document_id": part["document_id"],
                "part_title": part["part_title"],
                "text": part["text"],
                "part_index": part.get("part_index", 0),
            })
    logger.info(f"{len(parts)} document parts saved.")


async def insert_chunks(chunks: List[Dict[str, Any]]) -> None:
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
    async with get_connection() as cur:
        for chunk in chunks:
            await cur.execute(sql, {
                "id": chunk["id"],
                "document_part_id": chunk["document_part_id"],
                "text": chunk["text"],
                "embedding": chunk.get("embedding"),
                "chunk_metadata": json.dumps(chunk.get("chunk_metadata", {})),
            })

    logger.info(f"{len(chunks)} chunks saved to database.")


async def ingest_document_atomic(
    document: Dict[str, Any],
    parts: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
) -> None:
    """
    Inserts document + parts + chunks in a single transaction.
    Rolls back entirely if any insert fails, preventing orphaned documents.
    """
    doc_sql = """
        INSERT INTO documents (
            id, source_doc_id, title, act_type,
            doc_date, source_url, category, is_active
        )
        VALUES (
            %(id)s, %(source_doc_id)s, %(title)s, %(act_type)s,
            %(doc_date)s, %(source_url)s, %(category)s, %(is_active)s
        );
    """
    part_sql = """
        INSERT INTO document_parts (id, document_id, part_title, text, part_index)
        VALUES (%(id)s, %(document_id)s, %(part_title)s, %(text)s, %(part_index)s);
    """
    chunk_sql = """
        INSERT INTO chunks (id, document_part_id, text, embedding, chunk_metadata)
        VALUES (%(id)s, %(document_part_id)s, %(text)s, %(embedding)s, %(chunk_metadata)s);
    """

    chunk_params = [
        {
            "id": c["id"],
            "document_part_id": c["document_part_id"],
            "text": c["text"],
            "embedding": c.get("embedding"),
            "chunk_metadata": json.dumps(c.get("chunk_metadata", {})),
        }
        for c in chunks
    ]

    async with get_transaction() as cur:
        await cur.execute(doc_sql, document)
        if parts:
            await cur.executemany(part_sql, [
                {
                    "id": p["id"],
                    "document_id": p["document_id"],
                    "part_title": p["part_title"],
                    "text": p["text"],
                    "part_index": p.get("part_index", 0),
                }
                for p in parts
            ])
        if chunk_params:
            await cur.executemany(chunk_sql, chunk_params)

    logger.info(
        f"Atomic ingest complete: doc={document['source_doc_id']} "
        f"parts={len(parts)} chunks={len(chunks)}"
    )


# ── RAG Retrieval Queries ─────────────────────────────────────

async def search_similar_chunks(query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
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
    async with get_connection() as cur:
        await cur.execute(sql, (query_embedding, query_embedding, top_k))
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


async def fetch_document_parts(part_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetches full text for the given document_part UUIDs, joined with document metadata."""
    if not part_ids:
        return []

    sql = """
        SELECT dp.id AS part_id, dp.text AS full_markdown, dp.part_title AS title,
               dp.part_index, d.source_doc_id, d.source_url, d.title AS root_title
        FROM document_parts dp
        JOIN documents d ON dp.document_id = d.id
        WHERE dp.id = ANY(%s::uuid[])
        -- Deterministic order. Without it Postgres returned rows arbitrarily, and the
        -- citation builder (which kept the first row per document) showed a RANDOM part
        -- of the code — often not the one the answer was written from.
        ORDER BY d.title, dp.part_index;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (part_ids,))
        rows = cur.fetchall()

    return [
        {
            "id": str(row["part_id"]),
            "title": row["title"],
            "root_title": row["root_title"],
            "full_markdown": row["full_markdown"],
            "part_index": row["part_index"],
            "source_doc_id": row["source_doc_id"],
            "source_url": row["source_url"],
        }
        for row in rows
    ]


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _doc_meta_row(row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "source_doc_id": row["source_doc_id"],
        "title": row["title"],
        "act_type": row["act_type"],
        "doc_date": str(row["doc_date"]) if row["doc_date"] else None,
        "source_url": row["source_url"],
        "category": row["category"],
    }


async def resolve_legal_document(key: str) -> Optional[Dict[str, Any]]:
    """Resolves a citation key (a `source_doc_id`, a `documents.id`, or a stray
    `document_parts.id` — the panel sometimes only has the latter) to the root
    document's metadata.

    Tried as separate, short-circuited queries rather than one OR'd/UNION'd query:
    each branch is then a plain indexed lookup (source_doc_id is UNIQUE, both id
    columns are primary keys) instead of a query the planner can't use an index for,
    and the two UUID-keyed branches never even run for the common case of a plain
    `source_doc_id` string.
    """
    meta_cols = "id, source_doc_id, title, act_type, doc_date, source_url, category"

    async with get_connection() as cur:
        await cur.execute(
            f"SELECT {meta_cols} FROM documents WHERE source_doc_id = %s LIMIT 1;",
            (key,),
        )
        row = cur.fetchone()
        if row:
            return _doc_meta_row(row)

        if not _UUID_RE.match(key):
            return None

        await cur.execute(
            f"SELECT {meta_cols} FROM documents WHERE id = %s::uuid LIMIT 1;",
            (key,),
        )
        row = cur.fetchone()
        if row:
            return _doc_meta_row(row)

        await cur.execute(
            f"""
            SELECT d.id, d.source_doc_id, d.title, d.act_type, d.doc_date, d.source_url, d.category
            FROM document_parts dp
            JOIN documents d ON d.id = dp.document_id
            WHERE dp.id = %s::uuid
            LIMIT 1;
            """,
            (key,),
        )
        row = cur.fetchone()
        return _doc_meta_row(row) if row else None


async def fetch_document_parts_page(
    document_id: str, offset: int, limit: int
) -> Tuple[List[Dict[str, Any]], int]:
    """A window of a document's parts, with their full text, plus the total count."""
    async with get_connection() as cur:
        await cur.execute(
            "SELECT COUNT(*) AS n FROM document_parts WHERE document_id = %s::uuid;",
            (document_id,),
        )
        total = cur.fetchone()["n"]

        await cur.execute(
            """
            SELECT id, part_title, text, part_index
            FROM document_parts
            WHERE document_id = %s::uuid
            ORDER BY part_index ASC
            OFFSET %s LIMIT %s;
            """,
            (document_id, offset, limit),
        )
        rows = cur.fetchall()

    parts = [
        {
            "id": str(r["id"]),
            "part_title": r["part_title"],
            "text": r["text"],
            "part_index": r["part_index"],
        }
        for r in rows
    ]
    return parts, total


async def fetch_document_parts_meta(document_id: str) -> List[Dict[str, Any]]:
    """Part metadata only (no `text`) — the minimap needs positions and relative
    sizes for every part, but never needs to download a whole legal code to draw dots.
    """
    sql = """
        SELECT id, part_title, part_index, LENGTH(text) AS char_length
        FROM document_parts
        WHERE document_id = %s::uuid
        ORDER BY part_index ASC;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (document_id,))
        rows = cur.fetchall()

    return [
        {
            "id": str(r["id"]),
            "part_title": r["part_title"],
            "part_index": r["part_index"],
            "char_length": r["char_length"],
        }
        for r in rows
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
        "allow_data_collection": row.get("allow_data_collection", True),
        "terms_accepted_at": row.get("terms_accepted_at"),
        "created_at": row["created_at"],
        "last_login_at": row.get("last_login_at"),
    }


async def create_user(
    email: str,
    password_hash: str = None,
    full_name: str = None,
    auth_provider: str = "email",
    google_id: str = None,
    email_verified: bool = False,
    allow_data_collection: bool = True,
    terms_accepted_at: datetime = None,
) -> Optional[Dict[str, Any]]:
    sql = """
        INSERT INTO users (email, password_hash, full_name, auth_provider, google_id, email_verified, allow_data_collection, terms_accepted_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, email, password_hash, full_name, role, auth_provider,
                  google_id, email_verified, is_active, is_banned, allow_data_collection, terms_accepted_at, created_at, last_login_at;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (email, password_hash, full_name, auth_provider, google_id, email_verified, allow_data_collection, terms_accepted_at))
        row = cur.fetchone()
    return _user_row(row) if row else None


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, is_banned, allow_data_collection, terms_accepted_at, created_at, last_login_at
        FROM users WHERE email = %s;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (email,))
        row = cur.fetchone()
    return _user_row(row) if row else None


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, is_banned, allow_data_collection, terms_accepted_at, created_at, last_login_at
        FROM users WHERE id = %s::uuid;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return _user_row(row) if row else None


async def get_user_by_google_id(google_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a user by their Google ID."""
    sql = """
        SELECT id, email, password_hash, full_name, role, auth_provider,
               google_id, email_verified, is_active, is_banned, allow_data_collection, terms_accepted_at, created_at, last_login_at
        FROM users WHERE google_id = %s;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (google_id,))
        row = cur.fetchone()
    return _user_row(row) if row else None


# ── Verification Codes & Account Updates ───────────────────────

async def create_verification_code(email: str, code: str, code_type: str, expires_at: datetime, user_id: Optional[str] = None) -> None:
    """Inserts a new verification code, overwriting any previous one for the same email and type."""
    async with get_connection() as cur:
        # First delete any existing codes for this email and type
        await cur.execute(
            "DELETE FROM verification_codes WHERE email = %s AND type = %s;",
            (email, code_type)
        )
        sql = """
            INSERT INTO verification_codes (user_id, email, code, type, expires_at)
            VALUES (%s, %s, %s, %s, %s);
        """
        params = (user_id if user_id else None, email, code, code_type, expires_at)
        if user_id:
            sql = """
                INSERT INTO verification_codes (user_id, email, code, type, expires_at)
                VALUES (%s::uuid, %s, %s, %s, %s);
            """
        await cur.execute(sql, params)


async def get_verification_code(email: str, code: str, code_type: str) -> Optional[Dict[str, Any]]:
    """Returns the verification code if it exists and is not expired."""
    async with get_connection() as cur:
        sql = """
            SELECT * FROM verification_codes 
            WHERE email = %s AND code = %s AND type = %s AND expires_at > NOW();
        """
        await cur.execute(sql, (email, code, code_type))
        row = cur.fetchone()
    return dict(row) if row else None


async def delete_verification_codes_by_email(email: str, code_type: str) -> None:
    async with get_connection() as cur:
        await cur.execute("DELETE FROM verification_codes WHERE email = %s AND type = %s;", (email, code_type))


async def update_user_password(user_id: str, new_password_hash: str) -> None:
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s::uuid;",
            (new_password_hash, user_id)
        )


async def update_user_email(user_id: str, new_email: str) -> None:
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE users SET email = %s, email_verified = TRUE WHERE id = %s::uuid;",
            (new_email, user_id)
        )


async def unlink_google_id(user_id: str) -> None:
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE users SET google_id = NULL WHERE id = %s::uuid;",
            (user_id,)
        )


async def link_google_id(user_id: str, google_id: str) -> None:
    async with get_connection() as cur:
        await cur.execute("UPDATE users SET google_id = %s WHERE id = %s::uuid;", (google_id, user_id))


async def update_last_login(user_id: str) -> None:
    async with get_connection() as cur:
        await cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s::uuid;", (user_id,))


async def update_user_profile(user_id: str, full_name: str) -> None:
    async with get_connection() as cur:
        await cur.execute("UPDATE users SET full_name = %s WHERE id = %s::uuid;", (full_name, user_id))


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


async def create_session(user_id: str, title: str = "New Chat") -> Optional[Dict[str, Any]]:
    sql = """
        INSERT INTO chat_sessions (user_id, title)
        VALUES (%s::uuid, %s)
        RETURNING id, user_id, title, session_summary, is_pinned, created_at, updated_at;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (user_id, title))
        row = cur.fetchone()
    return _session_row(row) if row else None


async def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    sql = """
        SELECT id, user_id, title, session_summary, is_pinned, created_at, updated_at
        FROM chat_sessions
        WHERE user_id = %s::uuid
        ORDER BY is_pinned DESC, updated_at DESC;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (user_id,))
        rows = cur.fetchall()
    return [_session_row(r) for r in rows]


async def get_session_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT id, user_id, title, session_summary, is_pinned, created_at, updated_at
        FROM chat_sessions WHERE id = %s::uuid;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (session_id,))
        row = cur.fetchone()
    return _session_row(row) if row else None


async def update_session_summary(session_id: str, summary: str) -> None:
    sql = "UPDATE chat_sessions SET session_summary = %s, updated_at = NOW() WHERE id = %s::uuid;"
    async with get_connection() as cur:
        await cur.execute(sql, (summary, session_id))


async def rename_session(session_id: str, title: str) -> None:
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE chat_sessions SET title = %s, updated_at = NOW() WHERE id = %s::uuid;",
            (title, session_id)
        )


async def toggle_pin_session(session_id: str) -> None:
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE chat_sessions SET is_pinned = NOT is_pinned, updated_at = NOW() WHERE id = %s::uuid;",
            (session_id,)
        )


async def delete_session(session_id: str) -> None:
    async with get_connection() as cur:
        await cur.execute("DELETE FROM chat_sessions WHERE id = %s::uuid;", (session_id,))


async def insert_message(
    session_id: str, role: str, content: str,
    attachments: list = None, sources: Any = None,
) -> str:
    """
    Saves a chat message and returns its id.

    Attachments are stored as JSONB (document_id, display_name, mime_type, s3_key).
    `sources` holds citations, or a structured payload such as a comparison result.
    """
    import json
    attachments_json = json.dumps(attachments) if attachments else None
    sources_json = json.dumps(sources) if sources else None
    sql = """
        INSERT INTO chat_messages (session_id, role, content, attachments, sources)
        VALUES (%s::uuid, %s, %s, %s::jsonb, %s::jsonb)
        RETURNING id;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (session_id, role, content, attachments_json, sources_json))
        row = cur.fetchone()
        # Also touch the session
        await cur.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s::uuid;", (session_id,))
    return str(row["id"])


async def import_guest_session(
    user_id: str, title: str, messages: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Creates a session and inserts a batch of pre-validated messages atomically.

    Callers must have already filtered `attachments` down to document ids the user
    may read and constrained `role` to a value the `chat_messages` CHECK constraint
    accepts — this function trusts its input and only owns the transaction boundary,
    so a bad row can never leave a half-imported session behind.
    """
    session_sql = """
        INSERT INTO chat_sessions (user_id, title)
        VALUES (%(user_id)s::uuid, %(title)s)
        RETURNING id, user_id, title, session_summary, is_pinned, created_at, updated_at;
    """
    message_sql = """
        INSERT INTO chat_messages (session_id, role, content, attachments, sources)
        VALUES (%(session_id)s::uuid, %(role)s, %(content)s, %(attachments)s::jsonb, %(sources)s::jsonb);
    """

    async with get_transaction() as cur:
        await cur.execute(session_sql, {"user_id": user_id, "title": title})
        session_row = cur.fetchone()
        session_id = str(session_row["id"])

        if messages:
            await cur.executemany(message_sql, [
                {
                    "session_id": session_id,
                    "role": m["role"],
                    "content": m["content"],
                    "attachments": json.dumps(m["attachments"]) if m.get("attachments") else None,
                    "sources": json.dumps(m["sources"]) if m.get("sources") else None,
                }
                for m in messages
            ])

    return _session_row(session_row)


def _maybe_json(value: Any) -> Any:
    """asyncpg hands back jsonb as a string in some configurations."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


async def get_session_messages(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetches the last N messages for a session, ordered chronologically."""
    sql = """
        SELECT id, role, content, attachments, sources, created_at
        FROM chat_messages
        WHERE session_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT %s;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (session_id, limit))
        rows = cur.fetchall()

    # Reverse to return in chronological order
    result = []
    for row in reversed(rows):
        result.append({
            "id": str(row["id"]),
            "role": row["role"],
            "content": row["content"],
            "attachments": _maybe_json(row.get("attachments")) or None,
            # Returned so citations and comparison payloads survive a reload; the
            # column existed but was never selected, so they were lost cross-device.
            "sources": _maybe_json(row.get("sources")) or None,
            "created_at": row["created_at"].isoformat()
        })
    return result


async def delete_message(message_id: str) -> None:
    """Removes a single message. Used to roll back a pre-saved user turn when
    generation fails, so retrying does not leave a duplicate orphan in the chat."""
    async with get_connection() as cur:
        await cur.execute("DELETE FROM chat_messages WHERE id = %s::uuid;", (message_id,))


async def delete_oldest_message(session_id: str) -> Optional[Dict[str, Any]]:
    """Deletes and returns the oldest message in the session, useful for archiving shift."""
    sql = """
        DELETE FROM chat_messages 
        WHERE id = (
            SELECT id FROM chat_messages 
            WHERE session_id = %s::uuid 
            ORDER BY created_at ASC 
            LIMIT 1
        )
        RETURNING id, role, content, attachments, created_at;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (session_id,))
        row = cur.fetchone()

    if row:
        import json
        att = row.get("attachments")
        if isinstance(att, str):
            try:
                att = json.loads(att)
            except Exception:
                att = None
        return {
            "id": str(row["id"]),
            "role": row["role"],
            "content": row["content"],
            # Returned so the archive summariser can record that a document existed.
            # Without this, folding a message into the summary erased every trace of it.
            "attachments": att or None,
        }
    return None


# ── Uploaded Document Store ───────────────────────────────────
# Backs the attachment pipeline. See schema_unified.sql V6 for why this exists.

async def create_uploaded_document(
    *,
    user_id: Optional[str],
    display_name: str,
    original_mime: str,
    kind: str,
    extracted_text: Optional[str] = None,
    s3_key: Optional[str] = None,
    gemini_uri: Optional[str] = None,
    gemini_name: Optional[str] = None,
    gemini_mime: Optional[str] = None,
    gemini_expires_at: Optional[Any] = None,
) -> str:
    """Persists an uploaded document and returns its id."""
    sql = """
        INSERT INTO uploaded_documents
            (user_id, display_name, original_mime, kind, extracted_text, s3_key,
             gemini_uri, gemini_name, gemini_mime, gemini_expires_at)
        VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (
            user_id, display_name, original_mime, kind, extracted_text, s3_key,
            gemini_uri, gemini_name, gemini_mime, gemini_expires_at,
        ))
        row = cur.fetchone()
    return str(row["id"])


async def get_uploaded_documents(doc_ids: List[str]) -> List[Dict[str, Any]]:
    """Fetches documents by id. Order of the input list is preserved.

    Note: this performs NO ownership check — callers that accept ids straight from a
    client must use `assert_documents_owned` first. History rehydration is already
    gated by session ownership and can call this directly.
    """
    if not doc_ids:
        return []

    sql = """
        SELECT id, user_id, display_name, original_mime, kind, extracted_text,
               s3_key, gemini_uri, gemini_name, gemini_mime, gemini_expires_at
        FROM uploaded_documents
        WHERE id = ANY(%s::uuid[]);
    """
    async with get_connection() as cur:
        await cur.execute(sql, (list(doc_ids),))
        rows = cur.fetchall()

    by_id = {str(r["id"]): {**dict(r), "id": str(r["id"])} for r in rows}
    return [by_id[d] for d in doc_ids if d in by_id]


async def documents_not_owned_by(doc_ids: List[str], user_id: Optional[str]) -> List[str]:
    """Returns the subset of `doc_ids` this user may NOT read.

    Documents uploaded by guests have user_id NULL and are readable by anyone who
    holds the id (an unguessable UUID); documents owned by a *different* user are not.
    """
    if not doc_ids:
        return []

    sql = """
        SELECT id, user_id FROM uploaded_documents WHERE id = ANY(%s::uuid[]);
    """
    async with get_connection() as cur:
        await cur.execute(sql, (list(doc_ids),))
        rows = cur.fetchall()

    found = {str(r["id"]): (str(r["user_id"]) if r["user_id"] else None) for r in rows}
    bad = [d for d in doc_ids if d not in found]                      # missing entirely
    bad += [d for d, owner in found.items()
            if owner is not None and owner != (user_id or "")]        # someone else's
    return bad


async def update_document_gemini_handle(
    doc_id: str, gemini_uri: str, gemini_name: str,
    gemini_mime: str, gemini_expires_at: Any,
) -> None:
    """Caches a freshly minted Gemini Files handle for a media document."""
    sql = """
        UPDATE uploaded_documents
        SET gemini_uri = %s, gemini_name = %s, gemini_mime = %s, gemini_expires_at = %s
        WHERE id = %s::uuid;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (gemini_uri, gemini_name, gemini_mime, gemini_expires_at, doc_id))


# ── Usage Tracking Queries ────────────────────────────────────

async def get_daily_usage(user_id: str) -> int:
    async with get_connection() as cur:
        await cur.execute(
            "SELECT message_count FROM usage_logs WHERE user_id = %s::uuid AND usage_date = CURRENT_DATE;",
            (user_id,)
        )
        row = cur.fetchone()
    return row["message_count"] if row else 0


async def increment_usage(user_id: str) -> int:
    sql = """
        INSERT INTO usage_logs (user_id, usage_date, message_count)
        VALUES (%s::uuid, CURRENT_DATE, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET message_count = usage_logs.message_count + 1
        RETURNING message_count;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row["message_count"] if row else 1


async def get_guest_usage(fingerprint: str) -> int:
    async with get_connection() as cur:
        await cur.execute("SELECT message_count FROM guest_usage WHERE fingerprint = %s;", (fingerprint,))
        row = cur.fetchone()
    return row["message_count"] if row else 0


async def increment_guest_usage(fingerprint: str) -> int:
    sql = """
        INSERT INTO guest_usage (fingerprint, message_count)
        VALUES (%s, 1)
        ON CONFLICT (fingerprint)
        DO UPDATE SET message_count = guest_usage.message_count + 1,
                      last_seen_at = NOW()
        RETURNING message_count;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (fingerprint,))
        row = cur.fetchone()
    return row["message_count"] if row else 1


async def increment_upload_usage(user_id: str, upload_type: str) -> int:
    column = "image_upload_count" if upload_type == "image" else "doc_upload_count"
    sql = f"""
        INSERT INTO usage_logs (user_id, usage_date, {column})
        VALUES (%s::uuid, CURRENT_DATE, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET {column} = usage_logs.{column} + 1
        RETURNING {column};
    """
    async with get_connection() as cur:
        await cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row[column] if row else 1


async def increment_guest_upload_usage(fingerprint: str, upload_type: str) -> int:
    column = "image_upload_count" if upload_type == "image" else "doc_upload_count"
    sql = f"""
        INSERT INTO guest_usage (fingerprint, {column})
        VALUES (%s, 1)
        ON CONFLICT (fingerprint)
        DO UPDATE SET {column} = guest_usage.{column} + 1,
                      last_seen_at = NOW()
        RETURNING {column};
    """
    async with get_connection() as cur:
        await cur.execute(sql, (fingerprint,))
        row = cur.fetchone()
    return row[column] if row else 1


# ── Admin Queries ─────────────────────────────────────────────

async def get_all_users() -> List[Dict[str, Any]]:
    sql = """
        SELECT u.id, u.email, u.full_name, u.role, u.auth_provider,
               u.email_verified, u.is_active, u.created_at, u.last_login_at,
               COALESCE(ul.message_count, 0) AS today_usage,
               COALESCE(u.is_banned, FALSE) AS is_banned
        FROM users u
        LEFT JOIN usage_logs ul ON u.id = ul.user_id AND ul.usage_date = CURRENT_DATE
        ORDER BY u.created_at DESC;
    """
    async with get_connection() as cur:
        await cur.execute(sql)
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


async def update_user_role(user_id: str, role: str, admin_password_hash: Optional[str] = None) -> None:
    # root_admin can ONLY be assigned via direct database edit, never through the API
    if role not in ("guest", "free", "admin"):
        raise ValueError(f"Invalid role '{role}'. Promoting to root_admin is not allowed via API.")
    async with get_connection() as cur:
        if admin_password_hash is not None:
            await cur.execute(
                "UPDATE users SET role = %s, admin_password_hash = %s WHERE id = %s::uuid;",
                (role, admin_password_hash, user_id)
            )
        else:
            await cur.execute("UPDATE users SET role = %s WHERE id = %s::uuid;", (role, user_id))


async def delete_user(user_id: str) -> bool:
    """
    Hard-deletes a user and all their associated data (sessions, messages, usage logs).
    The user will need to register again. Returns True if a row was deleted.
    """
    async with get_connection() as cur:
        await cur.execute("DELETE FROM users WHERE id = %s::uuid RETURNING id;", (user_id,))
        row = cur.fetchone()
        return bool(row)


async def update_user_consent(user_id: str, allow_data_collection: bool, terms_accepted_at: datetime = None):
    """Updates user privacy consent and terms acceptance timestamp."""
    sql = "UPDATE users SET allow_data_collection = %s"
    params = [allow_data_collection]
    if terms_accepted_at:
        sql += ", terms_accepted_at = %s"
        params.append(terms_accepted_at)
    sql += " WHERE id = %s::uuid;"
    params.append(user_id)
    async with get_connection() as cur:
        await cur.execute(sql, tuple(params))


async def get_admin_stats() -> Dict[str, Any]:
    sql = """
        WITH
            user_count     AS (SELECT COUNT(*) AS n FROM users),
            doc_count      AS (SELECT COUNT(*) AS n FROM documents),
            part_count     AS (SELECT COUNT(*) AS n FROM document_parts),
            chunk_count    AS (SELECT COUNT(*) AS n FROM chunks),
            dau            AS (SELECT COUNT(DISTINCT user_id) AS n FROM usage_logs WHERE usage_date = CURRENT_DATE),
            daily_msgs     AS (SELECT COALESCE(SUM(message_count), 0) AS n FROM usage_logs WHERE usage_date = CURRENT_DATE)
        SELECT
            (SELECT n FROM user_count)   AS total_users,
            (SELECT n FROM doc_count)    AS total_documents,
            (SELECT n FROM part_count)   AS total_document_parts,
            (SELECT n FROM chunk_count)  AS total_chunks,
            (SELECT n FROM dau)          AS daily_active_users,
            (SELECT n FROM daily_msgs)   AS daily_messages;
    """
    async with get_connection() as cur:
        await cur.execute(sql)
        row = cur.fetchone()

    return {
        "total_users": row["total_users"],
        "total_documents": row["total_documents"],
        "total_document_parts": row["total_document_parts"],
        "total_chunks": row["total_chunks"],
        "daily_active_users": row["daily_active_users"],
        "daily_messages": row["daily_messages"],
    }


async def get_time_series_stats(range_val: str, target_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns time-series data for messages and active users.
    range_val: '1h', '24h', '7d', '30d', 'all'
    """
    if range_val == '10m':
        interval = "NOW() - INTERVAL '10 minutes'"
        trunc = "minute"
    elif range_val == '30m':
        interval = "NOW() - INTERVAL '30 minutes'"
        trunc = "minute"
    elif range_val == '1h':
        interval = "NOW() - INTERVAL '1 hour'"
        trunc = "minute"
    elif range_val == '24h':
        interval = "NOW() - INTERVAL '24 hours'"
        trunc = "hour"
    elif range_val == '7d':
        interval = "CURRENT_DATE - INTERVAL '6 days'"
        trunc = "day"
    elif range_val == '30d':
        interval = "CURRENT_DATE - INTERVAL '29 days'"
        trunc = "day"
    else:
        interval = "'2000-01-01'::timestamp"
        trunc = "day" if range_val != 'all' else "week"
        
    user_filter = "AND s.user_id = %s" if target_user_id else ""
    params = (target_user_id,) if target_user_id else ()

    sql = f"""
        SELECT 
            date_trunc('{trunc}', m.created_at) as period,
            COUNT(m.id) as message_count,
            COUNT(DISTINCT s.user_id) as active_users
        FROM chat_messages m
        JOIN chat_sessions s ON m.session_id = s.id
        WHERE m.created_at >= {interval}
        {user_filter}
        GROUP BY period
        ORDER BY period ASC;
    """
    
    async with get_connection() as cur:
        await cur.execute(sql, params)
        rows = cur.fetchall()
        
    return {
        "data": [
            {
                "period": row["period"].isoformat() if row["period"] else None,
                "message_count": row["message_count"],
                "active_users": row["active_users"]
            }
            for row in rows
        ]
    }


async def get_router_analytics_stats(range_val: str) -> Dict[str, Any]:
    """
    Returns RAG vs Conversational stats.
    """
    if range_val == '1h':
        interval = "NOW() - INTERVAL '1 hour'"
    elif range_val == '24h':
        interval = "NOW() - INTERVAL '24 hours'"
    elif range_val == '7d':
        interval = "CURRENT_DATE - INTERVAL '6 days'"
    elif range_val == '30d':
        interval = "CURRENT_DATE - INTERVAL '29 days'"
    else:
        interval = "'2000-01-01'::timestamp"

    sql = f"""
        SELECT 
            intent,
            COUNT(id) as count
        FROM router_analytics
        WHERE created_at >= {interval}
        GROUP BY intent;
    """
    async with get_connection() as cur:
        await cur.execute(sql)
        rows = cur.fetchall()
        
    stats = {row["intent"]: row["count"] for row in rows}
    total = sum(stats.values())
    
    return {
        "intents": stats,
        "total": total
    }

# ── Bulk Ingestion Jobs ──────────────────────────────────────

async def create_ingestion_jobs(urls: List[str]) -> List[Dict[str, Any]]:
    sql = """
        INSERT INTO ingestion_jobs (url, status)
        VALUES (%s, 'pending')
        RETURNING id, url, status, created_at;
    """
    jobs = []
    async with get_connection() as cur:
        for url in urls:
            await cur.execute(sql, (url,))
            row = cur.fetchone()
            jobs.append({
                "id": row["id"],
                "url": row["url"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None
            })
    return jobs

async def update_ingestion_job_status(job_id: str, status: str, error_message: Optional[str] = None):
    sql = """
        UPDATE ingestion_jobs 
        SET status = %s, error_message = %s, updated_at = NOW()
        WHERE id = %s::uuid;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (status, error_message, job_id))

async def get_ingestion_jobs() -> List[Dict[str, Any]]:
    sql = """
        SELECT id, url, status, error_message, created_at, updated_at
        FROM ingestion_jobs
        ORDER BY created_at DESC
        LIMIT 100;
    """
    async with get_connection() as cur:
        await cur.execute(sql)
        rows = cur.fetchall()
        
    return [
        {
            "id": row["id"],
            "url": row["url"],
            "status": row["status"],
            "error_message": row["error_message"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None
        }
        for row in rows
    ]

async def get_all_documents_admin() -> List[Dict[str, Any]]:
    sql = """
        SELECT d.id, d.source_doc_id, d.title, d.act_type, d.doc_date,
               d.source_url, d.category, d.is_active, d.created_at,
               COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN document_parts dp ON d.id = dp.document_id
        LEFT JOIN chunks c ON dp.id = c.document_part_id
        GROUP BY d.id
        ORDER BY d.created_at DESC;
    """
    async with get_connection() as cur:
        await cur.execute(sql)
        rows = cur.fetchall()

    return [
        {
            "id": str(r["id"]), "source_doc_id": r["source_doc_id"],
            "title": r["title"], "act_type": r["act_type"],
            "doc_date": r["doc_date"], "source_url": r["source_url"],
            "category": r["category"],
            "is_active": r["is_active"], "created_at": r["created_at"],
            "chunk_count": r["chunk_count"],
        }
        for r in rows
    ]


# ── System Settings Queries ──────────────────────────────────

async def get_all_settings() -> Dict[str, str]:
    async with get_connection() as cur:
        await cur.execute("SELECT key, value FROM system_settings;")
        rows = cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


_settings_cache: Dict[str, tuple] = {}  # key → (value, expires_at)
_SETTINGS_TTL = 45  # seconds


async def get_setting(key: str) -> Optional[str]:
    now = time.monotonic()
    if key in _settings_cache:
        value, expires_at = _settings_cache[key]
        if now < expires_at:
            return value
    async with get_connection() as cur:
        await cur.execute("SELECT value FROM system_settings WHERE key = %s;", (key,))
        row = cur.fetchone()
    value = row["value"] if row else None
    _settings_cache[key] = (value, now + _SETTINGS_TTL)
    return value


async def update_setting(key: str, value: str) -> None:
    sql = """
        INSERT INTO system_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (key, value))
    # Invalidate cached value so the next read is fresh
    _settings_cache.pop(key, None)


# ── Document Management Queries (Admin) ──────────────────────

async def get_document_full(doc_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a document, its parts count, and its full markdown content."""
    sql = """
        SELECT d.id, d.source_doc_id, d.title, d.act_type, d.doc_date,
               d.source_url, d.is_active, d.created_at,
               COUNT(dp.id) AS parts_count,
               STRING_AGG(dp.text, E'\\n\\n' ORDER BY dp.part_index) AS full_markdown
        FROM documents d
        LEFT JOIN document_parts dp ON d.id = dp.document_id
        WHERE d.id = %s::uuid
        GROUP BY d.id;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (doc_id,))
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": str(row["id"]), "source_doc_id": row["source_doc_id"],
        "title": row["title"], "act_type": row["act_type"],
        "doc_date": row["doc_date"], "source_url": row["source_url"],
        "is_active": row["is_active"], "parts_count": row["parts_count"],
        "created_at": row["created_at"], "full_markdown": row["full_markdown"] or "",
    }


async def update_document_metadata(doc_id: str, title: str, category: str, act_type: str) -> None:
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE documents SET title = %s, category = %s, act_type = %s WHERE id = %s::uuid;", 
            (title, category, act_type, doc_id)
        )


async def delete_document(doc_id: str) -> None:
    async with get_connection() as cur:
        await cur.execute("DELETE FROM documents WHERE id = %s::uuid;", (doc_id,))


# ── User Moderation Queries ──────────────────────────────────

async def toggle_ban_user(user_id: str) -> bool:
    sql = """
        UPDATE users SET is_banned = NOT COALESCE(is_banned, FALSE)
        WHERE id = %s::uuid
        RETURNING is_banned;
    """
    async with get_connection() as cur:
        await cur.execute(sql, (user_id,))
        row = cur.fetchone()
    return row["is_banned"] if row else False


async def get_user_stats(user_id: str) -> Dict[str, Any]:
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
    async with get_connection() as cur:
        await cur.execute(sql, (user_id, user_id, user_id, user_id))
        row = cur.fetchone()

    return {
        "daily_messages": row["daily_messages"],
        "weekly_messages": row["weekly_messages"],
        "total_messages": row["total_messages"],
        "session_count": row["session_count"],
    }

async def log_router_analytics(session_id: Optional[str], raw_query: str, intent: str, optimized_query: str, ideal_top_k: int, router_time_ms: Optional[int] = None, llm_time_ms: Optional[int] = None) -> None:
    if not session_id:
        return
    sql = """
        INSERT INTO router_analytics (session_id, raw_query, intent, optimized_query, ideal_top_k, router_time_ms, llm_time_ms)
        VALUES (%s::uuid, %s, %s, %s, %s, %s, %s)
    """
    async with get_connection() as cur:
        await cur.execute(sql, (session_id, raw_query, intent, optimized_query, ideal_top_k, router_time_ms, llm_time_ms))

async def get_avg_response_times(range_val: str) -> Dict[str, Any]:
    if range_val == '10m':
        interval = "NOW() - INTERVAL '10 minutes'"
    elif range_val == '30m':
        interval = "NOW() - INTERVAL '30 minutes'"
    elif range_val == '1h':
        interval = "NOW() - INTERVAL '1 hour'"
    elif range_val == '24h':
        interval = "NOW() - INTERVAL '24 hours'"
    elif range_val == '7d':
        interval = "CURRENT_DATE - INTERVAL '6 days'"
    elif range_val == '30d':
        interval = "CURRENT_DATE - INTERVAL '29 days'"
    else:
        interval = "'2000-01-01'::timestamp"

    sql = f"""
        SELECT 
            AVG(router_time_ms) as avg_router_time_ms,
            AVG(llm_time_ms) as avg_llm_time_ms
        FROM router_analytics
        WHERE created_at >= {interval}
    """
    async with get_connection() as cur:
        await cur.execute(sql)
        row = cur.fetchone()
        
    return {
        "avg_router_time_ms": int(row["avg_router_time_ms"]) if row and row["avg_router_time_ms"] else 0,
        "avg_llm_time_ms": int(row["avg_llm_time_ms"]) if row and row["avg_llm_time_ms"] else 0,
    }

# ── Auditing & Privacy ───────────────────────────────────────

async def get_all_sessions_audited(limit: int = 50, offset: int = 0) -> List[Dict]:
    """Returns a list of sessions, including user privacy status."""
    sql = """
        SELECT cs.id, cs.user_id, cs.created_at, cs.updated_at, cs.title,
               u.full_name, u.email, u.allow_data_collection
        FROM chat_sessions cs
        LEFT JOIN users u ON cs.user_id = u.id
        ORDER BY cs.updated_at DESC
        LIMIT %s OFFSET %s
    """
    async with get_connection() as cur:
        await cur.execute(sql, (limit, offset))
        rows = cur.fetchall()
        
    return [
        {
            "id": str(r["id"]),
            "user_id": str(r["user_id"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "title": r["title"],
            "full_name": r["full_name"],
            "email": r["email"],
            "allow_data_collection": r["allow_data_collection"]
        }
        for r in rows
    ]

async def get_session_messages_audited(session_id: str) -> List[Dict]:
    """
    Returns messages for a session.
    It checks if the user allowed data collection. If not, returns redacted messages.
    """
    sql_check = """
        SELECT u.allow_data_collection 
        FROM chat_sessions cs
        LEFT JOIN users u ON cs.user_id = u.id
        WHERE cs.id = %s
    """
    async with get_connection() as cur:
        await cur.execute(sql_check, (session_id,))
        row = cur.fetchone()
        
    allow_data = True
    if row and row.get('allow_data_collection') is False:
        allow_data = False

    sql_msgs = """
        SELECT id, role, content, created_at, sources
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY created_at ASC
    """
    async with get_connection() as cur:
        await cur.execute(sql_msgs, (session_id,))
        messages = cur.fetchall()
        
    result = []
    for m in messages:
        msg = dict(m)
        msg['id'] = str(msg['id'])
        msg['created_at'] = msg['created_at'].isoformat() if msg['created_at'] else None
        
        if not allow_data:
            if msg['role'] == 'user':
                msg['content'] = "[REDACTED - User opted out of data collection]"
            else:
                msg['content'] = "[REDACTED - User opted out of data collection]"
            msg['sources'] = []
        else:
            if isinstance(msg.get('sources'), str):
                import json
                try:
                    msg['sources'] = json.loads(msg['sources'])
                except:
                    msg['sources'] = []
        
        result.append(msg)
            
    return result

