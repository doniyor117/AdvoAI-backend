import pytest
import uuid
from app.ingestion.main_ingest import _split_markdown_into_parts, MAX_PART_SIZE, TARGET_SPLIT_SIZE
from app.ingestion.chunker import LegalUnstructuredChunker

def test_split_markdown_no_split():
    # If the markdown is smaller than MAX_PART_SIZE, it shouldn't split
    markdown = "A" * 1000
    title = "Test Doc"
    doc_uuid = str(uuid.uuid4())
    
    parts = _split_markdown_into_parts(markdown, title, doc_uuid)
    
    assert len(parts) == 1
    assert parts[0]["text"] == markdown
    assert parts[0]["part_title"] == title
    assert parts[0]["document_id"] == doc_uuid
    assert parts[0]["part_index"] == 0

def test_split_markdown_with_split():
    # If the markdown is larger than MAX_PART_SIZE, it should split at TARGET_SPLIT_SIZE boundaries
    markdown = "A" * (MAX_PART_SIZE + 1000)
    title = "Test Doc"
    doc_uuid = str(uuid.uuid4())
    
    parts = _split_markdown_into_parts(markdown, title, doc_uuid)
    
    assert len(parts) > 1
    assert len(parts[0]["text"]) >= TARGET_SPLIT_SIZE
    assert parts[0]["part_title"] == "Test Doc (Part 1)"
    assert parts[1]["part_title"] == "Test Doc (Part 2)"
    
    # Combined text should loosely match original (might strip some boundaries if the split logic did so)
    assert parts[0]["text"].startswith("A")

def test_chunker_basic():
    chunker = LegalUnstructuredChunker(max_characters=100, combine_text_under_n_chars=20)
    markdown = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    part_id = str(uuid.uuid4())
    
    chunks = chunker.chunk_markdown(markdown, part_id)
    
    assert len(chunks) > 0
    assert "First paragraph." in chunks[0]["text"]
    assert chunks[0]["document_part_id"] == part_id

def test_chunker_empty():
    chunker = LegalUnstructuredChunker(max_characters=100)
    chunks = chunker.chunk_markdown("   ", str(uuid.uuid4()))
    assert len(chunks) == 0
