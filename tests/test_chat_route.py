import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

from app.main import app
from app.middleware import require_rate_limit

# Mock authentication dependency
def override_require_rate_limit():
    return {"id": "test_user_id"}

app.dependency_overrides[require_rate_limit] = override_require_rate_limit

client = TestClient(app)

@patch('app.routes.chat.get_llm_client', new_callable=AsyncMock)
@patch('app.routes.chat.retrieve_context', new_callable=AsyncMock)
@patch('app.routes.chat.get_session_by_id', new_callable=AsyncMock)
@patch('app.routes.chat.get_session_messages', new_callable=AsyncMock)
@patch('app.database.queries.get_setting', new_callable=AsyncMock)
@patch('app.routes.chat.log_router_analytics', new_callable=AsyncMock)
def test_ask_advoai_search_intent(mock_log, mock_get_setting, mock_get_messages, mock_get_session, mock_retrieve, mock_get_llm):
    # Setup mocks
    mock_get_session.return_value = {"id": "session_123", "user_id": "test_user_id"}
    mock_get_messages.return_value = []
    
    mock_llm_instance = AsyncMock()
    mock_llm_instance.route_query.return_value = {"intent": "legal_rag", "search_query": "law search"}
    mock_llm_instance.ask.return_value = {
        "answer": "This is the RAG answer.",
        "model": "gemini-3.1",
        "context_length": 100,
        "prompt_length": 50
    }
    mock_get_llm.return_value = mock_llm_instance
    
    mock_retrieve.return_value = {
        "matched_chunks": [{"id": "chunk_1"}],
        "parent_documents": [
            {"source_doc_id": "doc_1", "title": "Doc 1", "full_markdown": "Markdown text", "source_url": "url"}
        ],
        "context_markdown": "Markdown text"
    }

    # Execute request
    response = client.post(
        "/api/chat/",
        json={"question": "What is the law?", "session_id": "session_123", "top_k": 3}
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "This is the RAG answer."
    assert data["intent"] == "legal_rag"
    assert data["model_used"] == "gemini-3.1"
    assert len(data["citations"]) == 1
    assert data["citations"][0]["id"] == "doc_1"

@patch('app.routes.chat.get_llm_client', new_callable=AsyncMock)
@patch('app.routes.chat.create_session', new_callable=AsyncMock)
@patch('app.routes.chat.get_session_messages', new_callable=AsyncMock)
@patch('app.database.queries.get_setting', new_callable=AsyncMock)
@patch('app.routes.chat.log_router_analytics', new_callable=AsyncMock)
def test_ask_advoai_conversational_intent(mock_log, mock_get_setting, mock_get_messages, mock_create_session, mock_get_llm):
    # Setup mocks
    mock_create_session.return_value = {"id": "session_new", "user_id": "test_user_id"}
    mock_get_messages.return_value = []
    
    mock_llm_instance = AsyncMock()
    mock_llm_instance.route_query.return_value = {"intent": "conversational", "search_query": ""}
    mock_llm_instance.ask.return_value = {
        "answer": "Hello! How can I help?",
        "model": "gemini-3.1",
        "context_length": 0,
        "prompt_length": 10
    }
    mock_get_llm.return_value = mock_llm_instance
    
    # Execute request
    response = client.post(
        "/api/chat/",
        json={"question": "Hi"}
    )
    
    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Hello! How can I help?"
    assert data["intent"] == "conversational"
    assert data["model_used"] == "gemini-3.1"
    assert len(data["citations"]) == 0
