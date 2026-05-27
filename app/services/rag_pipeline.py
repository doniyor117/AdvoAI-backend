"""
rag_pipeline.py — RAG Query Pipeline

The "brain" of the AdvoAI chatbot. Given a user's question:
    1. Embed the question using Gemini Embedding 2
    2. Search pgvector for the most similar chunks (HNSW cosine)
    3. Fetch the full parent Markdown documents
    4. Return the assembled context to send to Gemini LLM

"Small-to-Big" retrieval strategy:
    small chunks → accurate vector match → full parent document → rich LLM context
"""

import logging
from typing import List, Dict, Any

from app.services.embedder import get_embedder
from app.database.queries import search_similar_chunks, fetch_document_parts

logger = logging.getLogger(__name__)


def retrieve_context(question: str, top_k: int = 5) -> Dict[str, Any]:
    """
    The complete RAG retrieval pipeline.

    Args:
        question: The user's legal question in natural language.
        top_k:    Number of chunks to retrieve from pgvector.

    Returns:
        Dictionary with:
            - question:         Original question
            - matched_chunks:   Top-K similar chunks with similarity scores
            - parent_documents: Unique parent docs (full Markdown)
            - context_markdown: Combined doc text for the LLM prompt
    """
    logger.info(f"RAG retrieval | question: {question[:80]}...")

    # Step 1: Embed the query via Gemini Embedding 2
    embedder = get_embedder()
    query_embedding = embedder.embed_query(question)
    logger.info(f"Query embedded ({len(query_embedding)}-dim)")

    # Step 2: Vector search — top-K similar chunks
    matched_chunks = search_similar_chunks(query_embedding, top_k=top_k)
    logger.info(f"Matched {len(matched_chunks)} chunks")

    if matched_chunks:
        logger.info(f"Top similarity score: {matched_chunks[0]['similarity']:.4f}")

    # Step 3: Get unique document part IDs
    part_ids = list(set(chunk["document_part_id"] for chunk in matched_chunks))
    logger.info(f"Fetching {len(part_ids)} unique document parts (Mega-chunks)...")

    # Step 4: Fetch full text of document parts
    parent_documents = fetch_document_parts(part_ids)

    # Step 5: Combine all parent Markdown into one context string
    context_markdown = "\n\n---\n\n".join(
        doc["full_markdown"] for doc in parent_documents
    )

    logger.info(f"Context ready: {len(context_markdown):,} chars")

    return {
        "question": question,
        "matched_chunks": matched_chunks,
        "parent_documents": parent_documents,
        "context_markdown": context_markdown,
    }


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    test_question = "Какие штрафы предусмотрены за нарушение трудового договора?"
    print(f"\nTesting RAG pipeline...\n")

    result = retrieve_context(test_question, top_k=3)

    print(f"\nResults:")
    print(f"  Chunks found:    {len(result['matched_chunks'])}")
    print(f"  Documents found: {len(result['parent_documents'])}")
    print(f"  Context size:    {len(result['context_markdown']):,} chars")

    if result["matched_chunks"]:
        print(f"\nTop match (similarity={result['matched_chunks'][0]['similarity']}):")
        print(f"  {result['matched_chunks'][0]['text'][:150]}...")
