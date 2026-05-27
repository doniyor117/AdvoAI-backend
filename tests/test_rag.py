"""
test_rag.py — Simple integration tests for the new Gemini RAG pipeline.
"""
import pytest
from app.services.embedder import GeminiEmbedder
from app.services.llm_client import GeminiClient

def test_embedder_initialization():
    embedder = GeminiEmbedder()
    assert embedder.model == "gemini-embedding-2"
    assert embedder.dimensions == 1536

def test_embed_query_mock(monkeypatch):
    embedder = GeminiEmbedder()
    
    # Mock the internal client response
    class MockResponse:
        def __init__(self):
            self.embeddings = [type('obj', (object,), {'values': [0.1] * 1536})]
            
    def mock_embed_content(*args, **kwargs):
        return MockResponse()
        
    monkeypatch.setattr(embedder.client.models, "embed_content", mock_embed_content)
    
    vector = embedder.embed_query("How do I register a company?")
    assert len(vector) == 1536
    assert vector[0] == 0.1

def test_llm_client_initialization():
    client = GeminiClient(model_name="gemini-2.5-flash")
    assert client.model_name == "gemini-2.5-flash"
