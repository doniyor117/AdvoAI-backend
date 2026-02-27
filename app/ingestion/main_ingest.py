"""
main_ingest.py — Ingestion Pipeline Orchestrator

Ties the LexParser and LegalUnstructuredChunker together.
Fetches a Lex.uz document, produces a parent document record (full Markdown)
and a list of search chunks (for pgvector), linked by UUID-based parent_id.
"""

import re
import uuid
from typing import Dict, List, Any, Optional

from app.ingestion.lex_parser import LexParser
from app.ingestion.chunker import LegalUnstructuredChunker


def _extract_source_doc_id(url: str) -> Optional[str]:
    """
    Extracts the numeric document identifier from a Lex.uz URL.
    e.g. 'https://lex.uz/docs/-111189' → '111189'
         'https://lex.uz/docs/111189'  → '111189'
    """
    match = re.search(r"/docs/-?(\d+)", url)
    return match.group(1) if match else None


def process_and_ingest_law(url: str) -> Optional[Dict[str, Any]]:
    """
    End-to-end ingestion of a single Lex.uz legal document.

    Workflow:
        1. Fetch & clean HTML via LexParser
        2. Extract metadata + convert to Markdown
        3. Generate UUID for parent doc, extract source_doc_id from URL
        4. Build parent_document_record (simulated DB row)
        5. Chunk cleaned HTML for vector search index (chunks get their own UUIDs)
        6. Return both parent record and child chunks

    Args:
        url: A Lex.uz document URL (e.g. https://lex.uz/docs/-111189)

    Returns:
        Dictionary with 'parent_document' and 'search_chunks', or None on failure.
    """

    # ── Step 1: Fetch & Parse ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🚀 BASIRA INGESTION PIPELINE")
    print("=" * 60)

    parser = LexParser()

    if not parser.fetch_html(url):
        print("❌ Pipeline aborted: could not fetch HTML.")
        return None

    result = parser.parse()
    metadata: Dict[str, Any] = result["metadata"]
    markdown: str = result["markdown"]

    print(f"📋 Metadata extracted: doc_id={metadata.get('doc_id')}, "
          f"date={metadata.get('doc_date')}, type={metadata.get('act_type')}")
    print(f"📝 Markdown generated: {len(markdown):,} chars, "
          f"{markdown.count(chr(10)):,} lines")

    # ── Step 2: Generate IDs ──────────────────────────────────────────────
    # UUID4 = primary key (zero collision), source_doc_id = dedup key from URL
    doc_uuid: str = str(uuid.uuid4())
    source_doc_id: Optional[str] = _extract_source_doc_id(url)

    print(f"🔑 Document UUID (PK): {doc_uuid}")
    print(f"🔗 Source doc ID (dedup): {source_doc_id}")

    # ── Step 3: Build parent document record ──────────────────────────────
    # Simulates a row in the `documents` table (Neon PostgreSQL)
    parent_document_record: Dict[str, Any] = {
        "id": doc_uuid,                                         # UUID4 PK
        "source_doc_id": source_doc_id,                         # From URL, for dedup
        "title": metadata.get("title", "Untitled Document"),
        "metadata": metadata,
        "full_markdown": markdown,
    }
    print(f"💾 Parent document record prepared (title: '{parent_document_record['title'][:60]}...')")

    # ── Step 4: Chunk cleaned HTML for search index ───────────────────────
    cleaned_html: Optional[str] = str(parser.soup)
    if not cleaned_html:
        print("❌ Pipeline aborted: no cleaned HTML available for chunking.")
        return None

    chunker = LegalUnstructuredChunker(
        max_characters=1000,
        combine_text_under_n_chars=200
    )
    search_chunks: List[Dict[str, Any]] = chunker.chunk_html(cleaned_html, doc_uuid)

    # ── Step 5: Verify parent-child linkage ───────────────────────────────
    linked_ok = all(chunk["parent_id"] == doc_uuid for chunk in search_chunks)
    print(f"🔗 Parent-child linkage: {'✅ All chunks linked correctly' if linked_ok else '❌ LINKAGE ERROR'}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("📊 INGESTION SUMMARY")
    print("─" * 60)
    print(f"  Document:      {metadata.get('title', 'N/A')[:50]}")
    print(f"  UUID (PK):     {doc_uuid}")
    print(f"  Source ID:     {source_doc_id}")
    print(f"  Date:          {metadata.get('doc_date', 'N/A')}")
    print(f"  Act type:      {metadata.get('act_type', 'N/A')}")
    print(f"  Markdown:      {len(markdown):,} chars")
    print(f"  Chunks:        {len(search_chunks)}")
    print(f"  Linkage:       {'OK' if linked_ok else 'FAILED'}")
    print("─" * 60)

    if search_chunks:
        print("\n🔍 Sample chunks (first 3):")
        for chunk in search_chunks[:3]:
            preview = chunk["text"][:100].replace("\n", " ")
            print(f"  [{chunk['id'][:8]}...] {preview}...")

    return {
        "parent_document": parent_document_record,
        "search_chunks": search_chunks,
    }


if __name__ == "__main__":
    # Test the full pipeline with a Lex.uz document (Civil Code)
    test_url = "https://lex.uz/docs/-111189"
    print(f"\n🧪 Testing ingestion pipeline with: {test_url}\n")

    output = process_and_ingest_law(test_url)

    if output:
        print(f"\n✅ Pipeline completed successfully!")
        print(f"   Parent doc ready for `documents` table")
        print(f"   {len(output['search_chunks'])} chunks ready for `chunks` table (pgvector)")
    else:
        print("\n❌ Pipeline failed. Check errors above.")
