import pytest
import uuid
from unittest.mock import patch, MagicMock, AsyncMock
from app.ingestion.main_ingest import _split_markdown_into_parts, MAX_PART_SIZE, TARGET_SPLIT_SIZE, process_and_ingest_law
from app.ingestion.chunker import LegalUnstructuredChunker

@patch('app.ingestion.main_ingest.get_embedder')
@patch('app.ingestion.main_ingest.get_llm_client')
@patch('app.ingestion.main_ingest.LexParser')
@patch('app.ingestion.main_ingest.check_duplicate', new_callable=AsyncMock)
@patch('app.ingestion.main_ingest.ingest_document_atomic', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_process_and_ingest_law(mock_ingest_atomic, mock_check_dup, mock_extractor, mock_llm, mock_embedder):
    # Setup mocks
    mock_check_dup.return_value = False
    
    # Mocking parser
    mock_parser_instance = MagicMock()
    mock_parser_instance.fetch_html.return_value = True
    mock_parser_instance.parse.return_value = {
        "metadata": {"title": "Test Law"},
        "markdown": "This is a test law markdown.",
        "html": "<p>Test</p>"
    }
    mock_extractor.return_value = mock_parser_instance

    # Mocking LLM
    mock_llm_instance = AsyncMock()
    mock_llm_instance.extract_document_metadata.return_value = {"date": "2023-01-01"}
    mock_llm.return_value = mock_llm_instance

    # Mocking Embedder
    mock_embed_instance = AsyncMock()
    mock_embed_instance.embed_chunks.side_effect = lambda chunks, **kwargs: [
        dict(c, embedding=[0.1]) for c in chunks
    ]
    mock_embedder.return_value = mock_embed_instance
    
    # Execute
    await process_and_ingest_law("https://lex.uz/docs/12345")

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
