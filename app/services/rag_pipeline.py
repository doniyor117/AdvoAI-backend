"""
rag_pipeline.py — RAG Query Pipeline

The "brain" of the Yurika chatbot. Given a user's question:
    1. Embed the question using BGE-M3
    2. Search pgvector for the most similar chunks
    3. Fetch the full parent Markdown documents
    4. Return the context to send to Gemini

This is the "Small-to-Big" retrieval strategy:
    small chunks → accurate vector match → full document → rich LLM context
"""

from typing import List, Dict, Any

from app.config import settings
from app.services.embedder import get_embedder
from app.database.queries import search_similar_chunks, fetch_parent_documents


# ── Full RAG Pipeline ─────────────────────────────────────────

def retrieve_context(question: str, top_k: int = 5) -> Dict[str, Any]:
    """
    The complete RAG retrieval pipeline.

    1. Embed the user's question
    2. Search for similar chunks in pgvector
    3. Look up the parent documents (full Markdown)
    4. Return everything the LLM needs

    Args:
        question: The user's legal question in natural language.
        top_k:    Number of chunks to retrieve.

    Returns:
        Dictionary with 'question', 'matched_chunks', 'parent_documents',
        and 'context_markdown' (the combined text for Gemini).
    """
    print(f"\n🔍 RAG RETRIEVAL")
    print(f"   Question: {question[:80]}...")

    # Step 1: Embed the question
    embedder = get_embedder()
    query_output = embedder.model.encode(
        [question],
        batch_size=1,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False
    )
    query_embedding = query_output['dense_vecs'][0].tolist()
    print(f"   ✅ Question embedded ({len(query_embedding)}-dim)")

    # Step 2: Search for similar chunks AND optionally Rerank
    if settings.USE_RERANKER:
        print(f"   🔄 Cross-Encoder enabled. Fetching {top_k * 3} chunks for reranking...")
        # Get a wider net of candidates
        candidate_chunks = search_similar_chunks(query_embedding, top_k=(top_k * 3))
        
        # Lazy load reranker to save RAM if not used
        from app.services.reranker import get_reranker
        reranker = get_reranker()
        
        # Rerank and narrow down to top_k
        matched_chunks = reranker.rerank(query=question, chunks=candidate_chunks, top_k=top_k)
    else:
        print(f"   ⚡ Fast retrieval enabled (Reranker OFF). Fetching exactly {top_k} chunks...")
        matched_chunks = search_similar_chunks(query_embedding, top_k=top_k)

    print(f"   ✅ Final matched chunks: {len(matched_chunks)}")

    if matched_chunks:
        print(f"   📊 Top similarity: {matched_chunks[0]['similarity']:.4f}")

    # Step 3: Get unique parent document IDs (from the top chunks)
    parent_ids = list(set(chunk["parent_id"] for chunk in matched_chunks))
    print(f"   📄 Unique parent documents to fetch: {len(parent_ids)}")

    # Step 4: Fetch full parent documents
    parent_documents = fetch_parent_documents(parent_ids)

    # Step 5: Combine all parent Markdown into one context string
    context_markdown = "\n\n---\n\n".join(
        doc["full_markdown"] for doc in parent_documents
    )

    print(f"   ✅ Context ready: {len(context_markdown):,} chars for Gemini")

    return {
        "question": question,
        "matched_chunks": matched_chunks,
        "parent_documents": parent_documents,
        "context_markdown": context_markdown,
    }


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    test_question = "Какие штрафы предусмотрены за нарушение трудового договора?"
    print(f"\n🧪 Testing RAG pipeline...\n")

    result = retrieve_context(test_question, top_k=3)

    print(f"\n📊 Results:")
    print(f"   Chunks found:    {len(result['matched_chunks'])}")
    print(f"   Documents found: {len(result['parent_documents'])}")
    print(f"   Context size:    {len(result['context_markdown']):,} chars")

    if result["matched_chunks"]:
        print(f"\n🏆 Top match (similarity={result['matched_chunks'][0]['similarity']}):")
        print(f"   {result['matched_chunks'][0]['text'][:150]}...")
