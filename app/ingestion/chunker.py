import uuid
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class LegalUnstructuredChunker:
    """
    Takes a clean Markdown string and chunks it securely for Vector Search.
    These chunks are linked to a specific document_part_id (Mega-chunk).
    """
    
    def __init__(self, max_characters: int = 1000, combine_text_under_n_chars: int = 200):
        self.max_characters = max_characters
        self.combine_text_under_n_chars = combine_text_under_n_chars

    def chunk_markdown(self, markdown_string: str, document_part_id: str) -> List[Dict[str, Any]]:
        """
        Splits markdown into logical chunks. Attaches the document_part_id (UUID)
        so we can find the Mega-chunk Parent Document later via FK lookup.
        """
        if not markdown_string or not markdown_string.strip():
            logger.warning("Cannot chunk empty markdown string.")
            return []

        # Split by double newline first (paragraphs / block elements)
        paragraphs = [p.strip() for p in markdown_string.split('\n\n') if p.strip()]
        raw_chunks = []
        current_chunk_text = ""
        
        for p in paragraphs:
            if len(current_chunk_text) + len(p) + 2 <= self.max_characters:
                if current_chunk_text:
                    current_chunk_text += "\n\n" + p
                else:
                    current_chunk_text = p
            else:
                if current_chunk_text:
                    raw_chunks.append(current_chunk_text)
                
                # If a single block is huge, split by single newline
                if len(p) > self.max_characters:
                    lines = [line.strip() for line in p.split('\n') if line.strip()]
                    sub_chunk = ""
                    for line in lines:
                        if len(sub_chunk) + len(line) + 1 <= self.max_characters:
                            if sub_chunk:
                                sub_chunk += "\n" + line
                            else:
                                sub_chunk = line
                        else:
                            if sub_chunk: 
                                raw_chunks.append(sub_chunk)
                            sub_chunk = line
                    if sub_chunk:
                        current_chunk_text = sub_chunk
                else:
                    current_chunk_text = p
                    
        if current_chunk_text:
            raw_chunks.append(current_chunk_text)
            
        final_chunks: List[Dict[str, Any]] = []
        for text in raw_chunks:
            if len(text) < self.combine_text_under_n_chars and final_chunks:
                # Merge small trailing fragments with the previous chunk
                final_chunks[-1]["text"] += "\n\n" + text
            else:
                final_chunks.append({
                    "id": str(uuid.uuid4()),
                    "document_part_id": document_part_id,
                    "text": text,
                    "chunk_metadata": {} 
                })
        
        # Log chunk statistics
        if final_chunks:
            lengths = [len(c["text"]) for c in final_chunks]
            logger.info(f"Generated {len(final_chunks)} search chunks for part.")
            logger.info(f"Text lengths — avg: {sum(lengths)//len(lengths)} | min: {min(lengths)} | max: {max(lengths)} chars")
        else:
            logger.warning("No chunks generated.")

        return final_chunks