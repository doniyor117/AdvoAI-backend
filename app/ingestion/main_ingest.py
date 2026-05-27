"""
main_ingest.py — Ingestion Pipeline Orchestrator

End-to-end ingestion of Lex.uz legal documents:
    fetch → parse → chunk → embed → save to database

Uses GeminiEmbedder (gemini-embedding-2, 1536-dim) for vector generation.
"""

import logging
import re
import uuid
import argparse
from typing import Dict, List, Any, Optional

from app.ingestion.lex_parser import LexParser
from app.ingestion.chunker import LegalUnstructuredChunker
from app.services.embedder import get_embedder
from app.database.queries import check_duplicate, insert_document, insert_chunks
from app.config import settings

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────

def _extract_source_doc_id(url: str) -> Optional[str]:
    """
    Extracts the numeric document ID from a Lex.uz URL.

    Handles all URL variants:
        https://lex.uz/docs/-111189      -> '111189'
        https://lex.uz/docs/111189       -> '111189'
        https://lex.uz/en/docs/-7904841  -> '7904841'
    """
    match = re.search(r"/docs/-?(\d+)", url)
    return match.group(1) if match else None


# ── Main Pipeline ─────────────────────────────────────────────

def process_and_ingest_law(url: str, skip_db: bool = False) -> Optional[Dict[str, Any]]:
    """
    End-to-end ingestion of a single Lex.uz legal document.

    Pipeline:  fetch -> parse -> chunk -> embed -> save to DB

    Args:
        url:     A Lex.uz document URL (e.g. https://lex.uz/en/docs/-7904841)
        skip_db: If True, skip database writes (useful for testing without DB)

    Returns:
        Dictionary with 'parent_document' and 'search_chunks', or None on failure.
    """
    logger.info("=" * 60)
    logger.info("ADVOAI INGESTION PIPELINE")
    logger.info("=" * 60)

    # Step 1: Extract source_doc_id and check for duplicates
    source_doc_id: Optional[str] = _extract_source_doc_id(url)
    logger.info(f"Source doc ID: {source_doc_id}")

    if source_doc_id and not skip_db:
        if check_duplicate(source_doc_id):
            logger.info(f"Document '{source_doc_id}' already exists in DB. Skipping.")
            return None

    # Step 2: Fetch HTML and parse to Markdown
    parser = LexParser()
    if not parser.fetch_html(url):
        logger.error("Pipeline aborted: could not fetch HTML.")
        return None

    result = parser.parse()
    metadata: Dict[str, Any] = result["metadata"]
    markdown: str = result["markdown"]

    logger.info(
        f"Metadata: doc_id={metadata.get('doc_id')}, "
        f"date={metadata.get('doc_date')}, type={metadata.get('act_type')}"
    )
    logger.info(f"Markdown: {len(markdown):,} chars, {markdown.count(chr(10)):,} lines")

    # Step 3: Generate UUID and build parent document record
    doc_uuid: str = str(uuid.uuid4())
    doc_title: str = metadata.get("title", "Unknown")
    logger.info(f"UUID (PK): {doc_uuid}")

    parent_document_record: Dict[str, Any] = {
        "id": doc_uuid,
        "source_doc_id": source_doc_id,
        "title": doc_title,
        "act_type": metadata.get("act_type", "Unknown"),
        "doc_date": metadata.get("doc_date"),
        "source_url": url.split("?")[0],
        "is_active": metadata.get("is_active", True),
        "full_markdown": markdown,
    }

    # Step 4: Chunk cleaned HTML for search index
    cleaned_html: Optional[str] = str(parser.soup)
    if not cleaned_html:
        logger.error("Pipeline aborted: no cleaned HTML available.")
        return None

    chunker = LegalUnstructuredChunker(max_characters=1000, combine_text_under_n_chars=200)
    search_chunks: List[Dict[str, Any]] = chunker.chunk_html(cleaned_html, doc_uuid)

    # Step 5: Generate embeddings via Gemini Embedding 2
    embedder = get_embedder()
    # Pass the document title for better retrieval quality (used in task prompt)
    search_chunks = embedder.embed_chunks(search_chunks, doc_title=doc_title)

    # Step 6: Save to database
    if not skip_db:
        logger.info("Saving to database...")
        insert_document(parent_document_record)
        insert_chunks(search_chunks)
    else:
        logger.info("Skipping DB writes (skip_db=True)")

    # Step 7: Verify and summarize
    linked_ok = all(c["parent_id"] == doc_uuid for c in search_chunks)
    embedded_ok = all(
        "embedding" in c and len(c["embedding"]) == settings.EMBEDDING_DIMENSIONS
        for c in search_chunks
    )

    logger.info("-" * 60)
    logger.info("INGESTION SUMMARY")
    logger.info(f"  Document:    {doc_title[:50]}")
    logger.info(f"  UUID (PK):   {doc_uuid}")
    logger.info(f"  Source ID:   {source_doc_id}")
    logger.info(f"  Date:        {metadata.get('doc_date', 'N/A')}")
    logger.info(f"  Act type:    {metadata.get('act_type', 'N/A')}")
    logger.info(f"  Markdown:    {len(markdown):,} chars")
    logger.info(f"  Chunks:      {len(search_chunks)}")
    logger.info(f"  Embedded:    {'OK' if embedded_ok else 'FAILED'} ({settings.EMBEDDING_DIMENSIONS}-dim)")
    logger.info(f"  Linkage:     {'OK' if linked_ok else 'FAILED'}")
    logger.info(f"  Saved to DB: {'Yes' if not skip_db else 'Skipped'}")
    logger.info("-" * 60)

    return {
        "parent_document": parent_document_record,
        "search_chunks": search_chunks,
    }


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    from dotenv import load_dotenv
    load_dotenv()
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    arg_parser = argparse.ArgumentParser(description="AdvoAI Ingestion Pipeline")
    arg_parser.add_argument("--url", type=str, default="https://lex.uz/en/docs/-7904841", help="Document URL to ingest")
    arg_parser.add_argument("--skip-db", action="store_true", help="Skip saving to database")
    args = arg_parser.parse_args()

    logger.info(f"Testing ingestion pipeline with: {args.url}")

    output = process_and_ingest_law(args.url, skip_db=args.skip_db)

    if output:
        logger.info(f"Pipeline completed: {len(output['search_chunks'])} chunks ready for pgvector")
    else:
        logger.error("Pipeline failed. Check errors above.")
