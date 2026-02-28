"""
embedder.py — Legal Text Embedder (CPU-optimized)

Wraps the BGE-M3 model to generate 1024-dimensional dense vectors
for legal document chunks. Runs on CPU — no GPU required.

Usage:
    from app.services.embedder import LegalEmbedder

    embedder = LegalEmbedder(device="cpu")  # or "gpu"
    chunks_with_embeddings = embedder.embed_chunks(chunks)
"""

import os
import torch
from FlagEmbedding import BGEM3FlagModel
from typing import List, Dict, Any
from functools import lru_cache


class LegalEmbedder:
    """
    Wraps BGE-M3 to generate 1024-dim dense vectors for legal chunks.
    Forces CPU execution to avoid incompatible GPU issues.
    """

    def __init__(self, device: str = "cpu"):
        """
        Initialize the embedder.
        Args:
            device: 'cpu' (default, safe for conflicting GPUs) or 'gpu'.
        """
        self.device = device.lower()

        if self.device == "cpu":
            # Force CPU — avoids issues with incompatible GPUs (e.g. MX330 = CUDA 6.1)
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            use_fp16 = False
            device_str = "cpu"
        else:
            # GPU Mode
            use_fp16 = True
            device_str = None  # None lets FlagEmbedding auto-detect GPU

        print(f"🧠 Loading BGE-M3 model ({self.device.upper()} mode)...")
        self.model = BGEM3FlagModel(
            'BAAI/bge-m3',
            use_fp16=use_fp16,
            device=device_str,
            # Prevent network timeouts by forcing it to use the downloaded cache
            # local_files_only=True is supported by default in hf_hub
        )
        print("✅ Model loaded.")

    def embed_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Takes chunk dicts, embeds the text field, and attaches vectors.

        Args:
            chunks: List of dicts with at least a 'text' key.

        Returns:
            Same list with 'embedding' key added (list of 1024 floats).
        """
        if not chunks:
            return []

        print(f"🪄 Generating embeddings for {len(chunks)} chunks...")

        texts: List[str] = [chunk["text"] for chunk in chunks]

        try:
            # max_length is in TOKENS (not chars). BGE-M3 supports up to 8192.
            # batch_size=12 is safe for CPU. Reduce if you hit memory issues.
            output = self.model.encode(
                texts,
                batch_size=12,
                max_length=1024,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False
            )
        except Exception as err:
            print(f"❌ Embedding failed: {err}")
            raise

        dense_vectors = output['dense_vecs']

        # Attach vectors to chunks (convert numpy → Python list for pgvector)
        for i, chunk in enumerate(chunks):
            chunk["embedding"] = dense_vectors[i].tolist()

        print(f"✅ Embedded {len(chunks)} chunks ({len(dense_vectors[0])}-dim)")
        return chunks


@lru_cache(maxsize=1)
def get_embedder(device: str = "cpu") -> LegalEmbedder:
    """
    Returns a cached Singleton instance of the embedder.
    Prevents loading the 2.5GB model into RAM on every single request.
    """
    return LegalEmbedder(device=device)


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # Mock chunks matching chunker.py output format
    mock_chunks = [
        {"id": "aaa-111", "text": "The penalty for theft is up to 3 years.", "parent_id": "doc-uuid-1"},
        {"id": "bbb-222", "text": "Taxes must be paid by the end of the fiscal year.", "parent_id": "doc-uuid-1"}
    ]

    embedder = LegalEmbedder()
    embedded = embedder.embed_chunks(mock_chunks)

    print(f"\n📐 Dimensions: {len(embedded[0]['embedding'])}")   # Should be 1024
    print(f"🔢 First 5 values: {embedded[0]['embedding'][:5]}")
    print(f"🔗 Parent ID preserved: {embedded[0]['parent_id']}")