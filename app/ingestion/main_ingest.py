"""
main_ingest.py — Ingestion Pipeline Orchestrator

The complete ingestion pipeline for Lex.uz legal documents:
    parse → chunk → embed → save to database

Ties together: LexParser, LegalUnstructuredChunker, LegalEmbedder,
and the database layer (connection + queries).
"""

import re
import uuid
import argparse
from typing import Dict, List, Any, Optional

from app.ingestion.lex_parser import LexParser
from app.ingestion.chunker import LegalUnstructuredChunker
from app.services.embedder import get_embedder
from app.database.queries import check_duplicate, insert_document, insert_chunks


# ── Helpers ───────────────────────────────────────────────────

def _extract_source_doc_id(url: str) -> Optional[str]:
    """
    Extracts the numeric document ID from a Lex.uz URL.

    Handles all URL variants:
        https://lex.uz/docs/-111189      → '111189'
        https://lex.uz/docs/111189       → '111189'
        https://lex.uz/en/docs/-7904841  → '7904841'
    """
    match = re.search(r"/docs/-?(\d+)", url)
    return match.group(1) if match else None


# ── Main Pipeline ─────────────────────────────────────────────

def process_and_ingest_law(url: str, device: str = "cpu", skip_db: bool = False) -> Optional[Dict[str, Any]]:
    """
    End-to-end ingestion of a single Lex.uz legal document.

    Pipeline:  fetch → parse → chunk → embed → save to DB

    Args:
        url:     A Lex.uz document URL (e.g. https://lex.uz/en/docs/-7904841)
        device:  'cpu' or 'gpu' for embedding generation
        skip_db: If True, skip database writes (useful for testing without DB)

    Returns:
        Dictionary with 'parent_document' and 'search_chunks', or None on failure.
    """

    print("\n" + "=" * 60)
    print("🚀 BASIRA INGESTION PIPELINE")
    print("=" * 60)

    # ── Step 1: Extract source_doc_id and check for duplicates ────────────

    source_doc_id: Optional[str] = _extract_source_doc_id(url)
    print(f"🔗 Source doc ID: {source_doc_id}")

    if source_doc_id and not skip_db:
        if check_duplicate(source_doc_id):
            print(f"⏭️  Document '{source_doc_id}' already exists in DB. Skipping.")
            return None

    # ── Step 2: Fetch HTML and parse to Markdown ──────────────────────────

    parser = LexParser()

    if not parser.fetch_html(url):
        print("❌ Pipeline aborted: could not fetch HTML.")
        return None

    result = parser.parse()
    metadata: Dict[str, Any] = result["metadata"]
    markdown: str = result["markdown"]

    print(f"📋 Metadata: doc_id={metadata.get('doc_id')}, "
          f"date={metadata.get('doc_date')}, type={metadata.get('act_type')}")
    print(f"📝 Markdown: {len(markdown):,} chars, {markdown.count(chr(10)):,} lines")

    # ── Step 3: Generate UUID and build parent document record ────────────

    doc_uuid: str = str(uuid.uuid4())
    print(f"🔑 UUID (PK): {doc_uuid}")

    parent_document_record: Dict[str, Any] = {
        "id": doc_uuid,
        "source_doc_id": source_doc_id,
        "title": metadata.get("title", "Unknown"),
        "act_type": metadata.get("act_type", "Unknown"),
        "doc_date": metadata.get("doc_date"),
        "source_url": url.split("?")[0],
        "is_active": metadata.get("is_active", True),
        "full_markdown": markdown,
    }

    # ── Step 4: Chunk cleaned HTML for search index ───────────────────────

    cleaned_html: Optional[str] = str(parser.soup)
    if not cleaned_html:
        print("❌ Pipeline aborted: no cleaned HTML available.")
        return None

    chunker = LegalUnstructuredChunker(max_characters=1000, combine_text_under_n_chars=200)
    search_chunks: List[Dict[str, Any]] = chunker.chunk_html(cleaned_html, doc_uuid)

    # ── Step 5: Generate embeddings ───────────────────────────────────────

    embedder = get_embedder(device=device)
    search_chunks = embedder.embed_chunks(search_chunks)

    # ── Step 6: Save to database ──────────────────────────────────────────

    if not skip_db:
        print("💾 Saving to database...")
        insert_document(parent_document_record)
        insert_chunks(search_chunks)
    else:
        print("⏭️  Skipping DB writes (skip_db=True)")

    # ── Step 7: Verify and summarize ──────────────────────────────────────

    linked_ok = all(c["parent_id"] == doc_uuid for c in search_chunks)
    embedded_ok = all("embedding" in c and len(c["embedding"]) == 1024 for c in search_chunks)

    print("\n" + "─" * 60)
    print("📊 INGESTION SUMMARY")
    print("─" * 60)
    print(f"  Document:    {metadata.get('title', 'N/A')[:50]}")
    print(f"  UUID (PK):   {doc_uuid}")
    print(f"  Source ID:   {source_doc_id}")
    print(f"  Date:        {metadata.get('doc_date', 'N/A')}")
    print(f"  Act type:    {metadata.get('act_type', 'N/A')}")
    print(f"  Markdown:    {len(markdown):,} chars")
    print(f"  Chunks:      {len(search_chunks)}")
    print(f"  Embedded:    {'✅' if embedded_ok else '❌'}")
    print(f"  Linkage:     {'✅' if linked_ok else '❌'}")
    print(f"  Saved to DB: {'✅' if not skip_db else '⏭️ Skipped'}")
    print("─" * 60)

    if search_chunks:
        print("\n🔍 Sample chunks (first 3):")
        for chunk in search_chunks[:3]:
            preview = chunk["text"][:80].replace("\n", " ")
            print(f"  [{chunk['id'][:8]}...] {preview}...")

    return {
        "parent_document": parent_document_record,
        "search_chunks": search_chunks,
    }


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Basira Ingestion Pipeline")
    parser.add_argument("--url", type=str, default="https://lex.uz/en/docs/-7904841", help="Document URL to ingest")
    parser.add_argument("--device", type=str, choices=["cpu", "gpu"], default="cpu", help="Device to use for embedding (cpu or gpu)")
    parser.add_argument("--skip-db", action="store_true", help="Skip saving to database")
    args = parser.parse_args()

    print(f"\n🧪 Testing ingestion pipeline with: {args.url}")
    print(f"   Device: {args.device.upper()} | Skip DB: {args.skip_db}\n")

    output = process_and_ingest_law(args.url, device=args.device, skip_db=args.skip_db)

    if output:
        print(f"\n✅ Pipeline completed successfully!")
        print(f"   {len(output['search_chunks'])} chunks ready for pgvector")
    else:
        print("\n❌ Pipeline failed. Check errors above.")
