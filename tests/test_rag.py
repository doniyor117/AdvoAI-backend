import pytest
from unittest.mock import patch, MagicMock
from app.services.rag_pipeline import retrieve_context

@patch('app.services.rag_pipeline.get_embedder')
@patch('app.services.rag_pipeline.search_similar_chunks')
@patch('app.services.rag_pipeline.fetch_document_parts')
def test_retrieve_context(mock_fetch, mock_search, mock_embedder):
    # Mocking embedder
    mock_embed_instance = MagicMock()
    mock_embed_instance.embed_query.return_value = [0.1, 0.2, 0.3]
    mock_embedder.return_value = mock_embed_instance
    
    # Mocking search results
    mock_search.return_value = [
        {"document_part_id": "part_1", "similarity": 0.95},
        {"document_part_id": "part_1", "similarity": 0.90},  # Duplicate Mega-Chunk
        {"document_part_id": "part_2", "similarity": 0.85}
    ]
    
    # Mocking fetched parents
    mock_fetch.return_value = [
        {"source_doc_id": "doc_1", "title": "Test Title 1", "full_markdown": "Full mega-chunk 1 text"},
        {"source_doc_id": "doc_2", "title": "Test Title 2", "full_markdown": "Full mega-chunk 2 text"}
    ]
    
    result = retrieve_context("What is the civil code?", top_k=3)
    
    # Assert query embedded
    mock_embed_instance.embed_query.assert_called_once_with("What is the civil code?")
    
    # Assert search called
    mock_search.assert_called_once_with([0.1, 0.2, 0.3], top_k=3)
    
    # Assert deduplication: part_1 is matched twice, but we should only fetch unique parts
    fetch_args = mock_fetch.call_args[0][0]
    assert len(fetch_args) == 2
    assert "part_1" in fetch_args
    assert "part_2" in fetch_args
    
    # Assert output formatting
    assert result["question"] == "What is the civil code?"
    assert len(result["matched_chunks"]) == 3
    assert len(result["parent_documents"]) == 2
    assert "Full mega-chunk 1 text" in result["context_markdown"]
    assert "Full mega-chunk 2 text" in result["context_markdown"]

@patch('app.services.rag_pipeline.get_embedder')
@patch('app.services.rag_pipeline.search_similar_chunks')
@patch('app.services.rag_pipeline.fetch_document_parts')
def test_retrieve_context_no_results(mock_fetch, mock_search, mock_embedder):
    mock_embed_instance = MagicMock()
    mock_embed_instance.embed_query.return_value = [0.1]
    mock_embedder.return_value = mock_embed_instance
    
    mock_search.return_value = []
    
    result = retrieve_context("Unknown question", top_k=5)
    
    assert len(result["matched_chunks"]) == 0
    assert len(result["parent_documents"]) == 0
    assert result["context_markdown"] == ""
    mock_fetch.assert_called_once_with([])
