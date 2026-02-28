"""
reranker.py — Cross-Encoder Reranker

Implements the BAAI/bge-reranker-v2-m3 model.
Takes a user query and a list of retrieved chunks, and scores each pair
for exact semantic relevance. Slower than bi-encoders, but highly accurate.
"""

import os
from typing import List, Dict, Any
from FlagEmbedding import FlagReranker


class LegalReranker:
    """
    Wraps BGE-Reranker to re-score chunk relevance before passing
    parent documents to the LLM. Ensures accuracy over pure cosine similarity.
    """

    def __init__(self, device: str = "cpu"):
        self.device = device.lower()

        if self.device == "cpu":
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            use_fp16 = False
            device_str = "cpu"
        else:
            use_fp16 = True
            device_str = None

        print(f"⚖️ Loading BGE-Reranker model ({self.device.upper()} mode)...")
        # Initialize the FlagReranker wrapper from FlagEmbedding
        self.model = FlagReranker(
            'BAAI/bge-reranker-v2-m3',
            use_fp16=use_fp16,
            device=device_str
        )
        print("✅ Reranker model loaded.")

    def rerank(self, query: str, chunks: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Takes a list of chunk dictionaries and reranks them against the query.
        
        Args:
            query:  The user's original question.
            chunks: List of chunk dicts containing at least 'text' and 'chunk_id'.
            top_k:  How many of the perfectly rescored chunks to keep.
            
        Returns:
            The top_k chunk dicts, sorted by cross-encoder relevance,
            with their new 'similarity' score updated.
        """
        if not chunks:
            return []

        print(f"⚖️ Reranking {len(chunks)} chunks with Cross-Encoder...")

        # Build pairs: [[query, text1], [query, text2], ...]
        sentence_pairs = [[query, chunk["text"]] for chunk in chunks]

        # Compute cross-encoder scores
        try:
            scores = self.model.compute_score(sentence_pairs)
        except Exception as e:
            print(f"❌ Reranking failed: {e}")
            raise e

        # Handle edge case where compute_score returns a float (single item list)
        if isinstance(scores, float):
            scores = [scores]

        # Attach new scores to chunks
        for idx, chunk in enumerate(chunks):
            # Cross-encoder scores are logits (can be >1 or <0). 
            # We just use them for relative sorting.
            chunk["similarity"] = float(scores[idx])

        # Sort chunks by new score descending
        chunks.sort(key=lambda x: x["similarity"], reverse=True)

        # Slice off top_k
        top_chunks = chunks[:top_k]
        print(f"✅ Reranking complete. Top score: {top_chunks[0]['similarity']:.4f}")

        return top_chunks
