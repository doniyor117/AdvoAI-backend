"""
main_ingest.py — Ingestion Pipeline Orchestrator

End-to-end ingestion of Lex.uz legal documents:
    fetch → parse → split into mega-chunks → chunk → embed → save to database
"""

import logging
import re
import uuid
import argparse
from typing import Dict, List, Any, Optional

from app.ingestion.lex_parser import LexParser
from app.ingestion.chunker import LegalUnstructuredChunker
from app.services.embedder import get_embedder
from app.services.llm_client import get_llm_client
from app.database.queries import check_duplicate, insert_document, insert_document_parts, insert_chunks
from app.config import settings

logger = logging.getLogger(__name__)

# Constants for Dynamic Mega-Chunking
MAX_PART_SIZE = 100_000
TARGET_SPLIT_SIZE = 60_000

# ── Helpers ───────────────────────────────────────────────────

def _extract_source_doc_id(url: str) -> Optional[str]:
    """
    Extracts the numeric document ID from a Lex.uz URL.
    """
    match = re.search(r"/docs/-?(\d+)", url)
    return match.group(1) if match else None

def _split_markdown_into_parts(markdown: str, title: str, doc_uuid: str) -> List[Dict[str, Any]]:
    """Splits a massive markdown document into 60k character Mega-Chunks."""
    if len(markdown) <= MAX_PART_SIZE:
        return [{
            "id": str(uuid.uuid4()),
            "document_id": doc_uuid,
            "part_title": title,
            "text": markdown,
            "part_index": 0
        }]
    
    parts = []
    current_idx = 0
    part_idx = 0
    
    while current_idx < len(markdown):
        end_idx = current_idx + TARGET_SPLIT_SIZE
        if end_idx >= len(markdown):
            parts.append({
                "id": str(uuid.uuid4()),
                "document_id": doc_uuid,
                "part_title": f"{title} (Part {part_idx + 1})",
                "text": markdown[current_idx:].strip(),
                "part_index": part_idx
            })
            break
            
        # Try to find a clean break (double newline)
        clean_break = markdown.find("\n\n", end_idx)
        if clean_break != -1 and clean_break - end_idx < 10000:
            end_idx = clean_break
        else:
            # Fallback backward search
            clean_break = markdown.rfind("\n\n", current_idx, end_idx)
            if clean_break != -1 and clean_break > current_idx:
                end_idx = clean_break
                
        part_text = markdown[current_idx:end_idx].strip()
        if part_text:
            parts.append({
                "id": str(uuid.uuid4()),
                "document_id": doc_uuid,
                "part_title": f"{title} (Part {part_idx + 1})",
                "text": part_text,
                "part_index": part_idx
            })
            part_idx += 1
            
        current_idx = end_idx
        
    return parts

# ── Main Pipeline ─────────────────────────────────────────────

async def process_and_ingest_law(url: str, skip_db: bool = False) -> Optional[Dict[str, Any]]:
    """
    End-to-end ingestion of a single Lex.uz legal document.
    """
    logger.info("=" * 60)
    logger.info("ADVOAI INGESTION PIPELINE")
    logger.info("=" * 60)

    # Step 1: Extract source_doc_id and check for duplicates
    source_doc_id: Optional[str] = _extract_source_doc_id(url)
    logger.info(f"Source doc ID: {source_doc_id}")

    if source_doc_id and not skip_db:
        if await check_duplicate(source_doc_id):
            logger.info(f"Document '{source_doc_id}' already exists in DB. Skipping.")
            return None

    # Step 2: Fetch HTML and parse to Markdown
    parser = LexParser()
    if not parser.fetch_html(url):
        logger.error("Pipeline aborted: could not fetch HTML.")
        return None

    result = parser.parse()
    base_metadata: Dict[str, Any] = result["metadata"]
    markdown: str = result["markdown"]

    # Step 2.5: Extract structured metadata using LLM
    logger.info("Extracting metadata using LLM...")
    header_text = markdown[:2500]
    llm_client = await get_llm_client()
    llm_metadata = await llm_client.extract_document_metadata(header_text)
    
    # Merge metadata
    metadata = {**base_metadata, **llm_metadata}

    logger.info(
        f"Metadata: doc_id={metadata.get('doc_id')}, "
        f"date={metadata.get('doc_date')}, type={metadata.get('act_type')}"
    )
    logger.info(f"Markdown: {len(markdown):,} chars, {markdown.count(chr(10)):,} lines")

    # Step 3: Generate UUID and build ROOT document record
    doc_uuid: str = str(uuid.uuid4())
    doc_title: str = metadata.get("title", "Unknown")
    logger.info(f"UUID (Root PK): {doc_uuid}")

    # Fallback to source doc ID if LLM couldn't find one
    final_source_doc_id = metadata.get("doc_id") or source_doc_id

    root_document_record: Dict[str, Any] = {
        "id": doc_uuid,
        "source_doc_id": final_source_doc_id,
        "title": doc_title,
        "act_type": metadata.get("act_type", "Unknown"),
        "doc_date": metadata.get("doc_date"),
        "source_url": base_metadata.get("source_url", url.split("?")[0]),
        "is_active": base_metadata.get("is_active", True),
    }

    # Step 4: Split into Mega-Chunks (document_parts)
    document_parts = _split_markdown_into_parts(markdown, doc_title, doc_uuid)
    logger.info(f"Split document into {len(document_parts)} Mega-Chunks (Parts).")

    # Step 5: Chunk the Markdown parts for vector search
    chunker = LegalUnstructuredChunker(max_characters=1000, combine_text_under_n_chars=200)
    all_search_chunks: List[Dict[str, Any]] = []
    
    for part in document_parts:
        part_chunks = chunker.chunk_markdown(part["text"], document_part_id=part["id"])
        all_search_chunks.extend(part_chunks)

    # Step 6: Generate embeddings
    embedder = get_embedder()
    # Note: passing doc_title helps gemini embeddings contextualize the chunk
    all_search_chunks = await embedder.embed_chunks(all_search_chunks, doc_title=doc_title)

    # Step 7: Save to database
    if not skip_db:
        logger.info("Saving to database...")
        await insert_document(root_document_record)
        await insert_document_parts(document_parts)
        await insert_chunks(all_search_chunks)
    else:
        logger.info("Skipping DB writes (skip_db=True)")

    # Step 8: Verify and summarize
    embedded_ok = all(
        "embedding" in c and len(c["embedding"]) == settings.EMBEDDING_DIMENSIONS
        for c in all_search_chunks
    )

    logger.info("-" * 60)
    logger.info("INGESTION SUMMARY")
    logger.info(f"  Document:    {doc_title[:50]}")
    logger.info(f"  Root UUID:   {doc_uuid}")
    logger.info(f"  Source ID:   {source_doc_id}")
    logger.info(f"  Markdown:    {len(markdown):,} chars")
    logger.info(f"  Mega-Chunks: {len(document_parts)} parts")
    logger.info(f"  Vector Chnks:{len(all_search_chunks)}")
    logger.info(f"  Embedded:    {'OK' if embedded_ok else 'FAILED'} ({settings.EMBEDDING_DIMENSIONS}-dim)")
    logger.info(f"  Saved to DB: {'Yes' if not skip_db else 'Skipped'}")
    logger.info("-" * 60)

    return {
        "parent_document": root_document_record,
        "document_parts": document_parts,
        "search_chunks": all_search_chunks,
    }


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    from dotenv import load_dotenv
    load_dotenv()
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    from app.database.connection import init_pool
    init_pool()

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
