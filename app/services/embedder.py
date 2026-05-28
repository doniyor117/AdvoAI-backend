"""
embedder.py — Gemini Embedding 2 Service

Wraps Google's gemini-embedding-2 API to generate 1536-dimensional
dense vectors for legal document chunks and search queries.

Key differences from the old BGE-M3 approach:
  - Zero local compute: no model download, no torch, no GPU
  - Uses inline task instructions in the prompt (not task_type parameter)
  - Query format:    "task: question answering | query: {text}"
  - Document format: "title: {title} | text: {content}"
  - Each chunk wrapped in Content() to get individual embeddings
  - 8,192 token input limit (vs BGE-M3's 2,048)
  - Auto-normalization for non-default dimensions

Ref: https://ai.google.dev/gemini-api/docs/embeddings
"""

import logging
from functools import lru_cache
from typing import List, Dict, Any
import asyncio

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)


class GeminiEmbedder:
    """
    Wraps gemini-embedding-2 to generate 1536-dim dense vectors.
    Uses the same GOOGLE_API_KEY already configured for the LLM client.
    """

    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.model = settings.EMBEDDING_MODEL         # "gemini-embedding-2"
        self.dimensions = settings.EMBEDDING_DIMENSIONS  # 1536
        logger.info(
            f"GeminiEmbedder initialized: model={self.model}, dimensions={self.dimensions}"
        )

    async def embed_query(self, text: str) -> List[float]:
        """
        Embeds a user search query.

        Uses "task: question answering" prefix per gemini-embedding-2 docs.
        This asymmetric format optimises the query vector for retrieval.

        Args:
            text: The user's legal question.

        Returns:
            List of floats (length = settings.EMBEDDING_DIMENSIONS).
        """
        formatted = f"task: question answering | query: {text}"
        result = await self.client.aio.models.embed_content(
            model=self.model,
            contents=formatted,
            config=types.EmbedContentConfig(
                output_dimensionality=self.dimensions
            ),
        )
        return list(result.embeddings[0].values)

    async def embed_chunks(
        self,
        chunks: List[Dict[str, Any]],
        doc_title: str = "none",
    ) -> List[Dict[str, Any]]:
        """
        Embeds a list of document chunks.

        Uses document format: "title: {title} | text: {content}"
        Each chunk is wrapped in a Content object so the API returns
        individual embeddings (not a single aggregated one).

        Args:
            chunks:    List of dicts with at least a 'text' key.
            doc_title: The parent document title (improves retrieval quality).

        Returns:
            Same list with 'embedding' key added (list of 1536 floats).
        """
        if not chunks:
            return []

        logger.info(f"Generating embeddings for {len(chunks)} chunks (model={self.model})...")

        # Wrap each chunk in a Content object → individual embeddings per chunk
        contents = [
            types.Content(
                parts=[types.Part(
                    text=f"title: {doc_title} | text: {chunk['text']}"
                )]
            )
            for chunk in chunks
        ]

        # Google Gemini API has a hard limit of 100 requests per batch.
        batch_size = 100
        all_embeddings = []

        try:
            for i in range(0, len(contents), batch_size):
                batch_contents = contents[i:i + batch_size]
                logger.debug(f"Embedding batch {i // batch_size + 1} of {(len(contents) - 1) // batch_size + 1}...")

                retries = 5
                backoff = 30
                for attempt in range(retries):
                    try:
                        result = await self.client.aio.models.embed_content(
                            model=self.model,
                            contents=batch_contents,
                            config=types.EmbedContentConfig(
                                output_dimensionality=self.dimensions
                            ),
                        )
                        for e in result.embeddings:
                            all_embeddings.append(list(e.values))
                        break  # Batch succeeded, proceed to next batch
                    except Exception as err:
                        err_str = str(err)
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                            if attempt == retries - 1:
                                raise
                            
                            # Determine sleep time (parse Google API's suggested retry window if present)
                            sleep_seconds = backoff
                            import re
                            # Try matching 'retryDelay': '44s' or 'retryDelay: "44s"'
                            match = re.search(r"retryDelay['\"]:\s*['\"](\d+)s['\"]", err_str)
                            if match:
                                sleep_seconds = int(match.group(1)) + 2  # add safety buffer
                            else:
                                # Try matching 'Please retry in 44.2297s'
                                match_float = re.search(r"retry in (\d+\.?\d*)s", err_str)
                                if match_float:
                                    sleep_seconds = int(float(match_float.group(1))) + 2
                            
                            logger.warning(
                                f"Gemini API rate limit (429) hit. Sleeping for {sleep_seconds}s before retrying batch {i // batch_size + 1} (attempt {attempt + 1}/{retries})..."
                            )
                            await asyncio.sleep(sleep_seconds)
                            backoff *= 2  # Double the backoff window for next attempt
                        else:
                            raise

        except Exception as err:
            logger.error(f"Embedding API call failed: {err}")
            raise

        for i, chunk in enumerate(chunks):
            chunk["embedding"] = all_embeddings[i]

        logger.info(
            f"Embedded {len(chunks)} chunks ({self.dimensions}-dim)"
        )
        return chunks


@lru_cache(maxsize=1)
def get_embedder() -> GeminiEmbedder:
    """
    Returns a cached singleton GeminiEmbedder.
    Safe to call multiple times — only one client is created.
    """
    return GeminiEmbedder()


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    mock_chunks = [
        {"id": "aaa-111", "text": "The penalty for theft is up to 3 years.", "parent_id": "doc-uuid-1"},
        {"id": "bbb-222", "text": "Taxes must be paid by the end of the fiscal year.", "parent_id": "doc-uuid-1"},
    ]

    embedder = get_embedder()

    print("\n--- Testing embed_chunks ---")
    embedded = embedder.embed_chunks(mock_chunks, doc_title="Criminal Code of Uzbekistan")
    print(f"Dimensions: {len(embedded[0]['embedding'])}")  # Should be 1536
    print(f"First 5 values: {embedded[0]['embedding'][:5]}")

    print("\n--- Testing embed_query ---")
    q_vec = embedder.embed_query("What is the punishment for theft?")
    print(f"Query embedding dimensions: {len(q_vec)}")