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
        SELECT id, title, full_markdown
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
        })

    return documents


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🧪 Testing database queries...\n")

    # Test dedup check
    test_id = "111189"
    exists = check_duplicate(test_id)
    print(f"Document '{test_id}' exists: {exists}")
