import uuid

from unstructured.partition.html import partition_html
from unstructured.chunking.title import chunk_by_title
from typing import List, Dict, Any

class LegalUnstructuredChunker:
    """
    Takes the cleaned HTML string, partitions it, and chunks it using unstructured.
    These chunks are strictly used for Vector Search, NOT for the LLM context.
    """
    
    def __init__(self, max_characters: int = 1000, combine_text_under_n_chars: int = 200):
        self.max_characters = max_characters
        self.combine_text_under_n_chars = combine_text_under_n_chars

    def chunk_html(self, html_string: str, parent_id: str) -> List[Dict[str, Any]]:
        """
        Partitions HTML and chunks it. Attaches the parent_id (UUID) so we can 
        find the full Markdown document later via FK lookup.
        """
        if not html_string or not html_string.strip():
            raise ValueError("❌ Cannot chunk empty HTML string.")

        print("🧩 Partitioning HTML into unstructured elements...")
        elements = partition_html(text=html_string)
        
        if not elements:
            print("⚠️  Warning: No elements found after partitioning HTML.")
            return []

        print("🔪 Chunking elements by title...")
        chunks = chunk_by_title(
            elements=elements,
            max_characters=self.max_characters,
            combine_text_under_n_chars=self.combine_text_under_n_chars
        )
        
        final_chunks: List[Dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            final_chunks.append({
                "id": str(uuid.uuid4()),           # UUID PK for this chunk
                "parent_id": parent_id,             # FK → documents.id (UUID)
                "text": chunk.text,
                "chunk_metadata": chunk.metadata.to_dict() if hasattr(chunk.metadata, 'to_dict') else {} 
            })
        
        # Log chunk statistics
        if final_chunks:
            lengths = [len(c["text"]) for c in final_chunks]
            print(f"✅ Generated {len(final_chunks)} search chunks.")
            print(f"   📊 Text lengths — avg: {sum(lengths)//len(lengths)} | min: {min(lengths)} | max: {max(lengths)} chars")
        else:
            print("⚠️  No chunks generated.")

        return final_chunks