import pytest
from unittest.mock import patch, MagicMock
from app.services.llm_client import GeminiClient
from google.genai import types

@patch('app.services.llm_client.genai.Client')
def test_route_query_conversational(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.candidates = None
    mock_response.text = '{"intent": "conversational"}'
    mock_instance.models.generate_content.return_value = mock_response
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routing_data = client.route_query("Hi there")
    
    assert routing_data["intent"] == "conversational"
    assert routing_data["search_query"] == ""
    mock_instance.models.generate_content.assert_called_once()
    args, kwargs = mock_instance.models.generate_content.call_args
    assert kwargs["model"] == "gemma-4"

@patch('app.services.llm_client.genai.Client')
def test_route_query_legal_rag(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.candidates = None
    mock_response.text = '{"intent": "legal_rag", "search_query": "contract penalty"}'
    mock_instance.models.generate_content.return_value = mock_response
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routing_data = client.route_query("What is the penalty for breaching a contract?")
    
    assert routing_data["intent"] == "legal_rag"
    assert routing_data["search_query"] == "contract penalty"

@patch('app.services.llm_client.genai.Client')
def test_ask_method(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.text = "This is the answer"
    mock_response.usage_metadata.candidates_token_count = 10
    mock_response.usage_metadata.prompt_token_count = 5
    mock_instance.models.generate_content.return_value = mock_response
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    
    # Passing structured history
    history = [
        {"role": "user", "content": "Question 1"},
        {"role": "assistant", "content": "Answer 1"}
    ]
    
    result = client.ask(
        question="Question 2",
        structured_history=history,
        context_markdown="Context",
        session_summary="Summary",
        is_conversational=False
    )
    
    assert result["answer"] == "This is the answer"
    assert result["model"] == "gemini-3.1"
    
    args, kwargs = mock_instance.models.generate_content.call_args
    assert kwargs["model"] == "gemini-3.1"
    
    # Assert structured history was mapped correctly
    contents = kwargs["contents"]
    assert len(contents) == 3 # 2 history + 1 current question
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"
    assert contents[2]["role"] == "user"
    
    # Ensure context markdown is in the final message
    assert "Context" in contents[2]["parts"][0]["text"]

@patch('app.services.llm_client.genai.Client')
def test_ask_conversational(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.text = "Hello!"
    mock_instance.models.generate_content.return_value = mock_response
    
    client = GeminiClient(main_model="gemini-3.1")
    
    result = client.ask(
        question="Hello",
        structured_history=[],
        context_markdown="",
        is_conversational=True
    )
    
    assert result["answer"] == "Hello!"
