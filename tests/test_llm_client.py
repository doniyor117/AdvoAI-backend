import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.llm_client import GeminiClient
from google.genai import types

@pytest.mark.asyncio
@patch('app.services.llm_client.genai.Client')
async def test_route_query_conversational(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.candidates = None
    mock_response.text = '{"intent": "conversational"}'
    mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routing_data = await client.route_query("Hi there")
    
    assert routing_data["intent"] == "conversational"
    assert routing_data["search_query"] == ""
    mock_instance.aio.models.generate_content.assert_called_once()
    args, kwargs = mock_instance.aio.models.generate_content.call_args
    assert kwargs["model"] == "gemma-4"

@pytest.mark.asyncio
@patch('app.services.llm_client.genai.Client')
async def test_route_query_legal_rag(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.candidates = None
    mock_response.text = '{"intent": "legal_rag", "search_query": "contract penalty"}'
    mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routing_data = await client.route_query("What is the penalty for breaching a contract?")
    
    assert routing_data["intent"] == "legal_rag"
    assert routing_data["search_query"] == "contract penalty"

@pytest.mark.asyncio
@patch('app.services.llm_client.genai.Client')
async def test_route_query_with_history(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.candidates = None
    mock_response.text = '{"intent": "legal_rag", "search_query": "civil code details"}'
    mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    history = [{"role": "assistant", "content": "The Civil Code is..."}, {"role": "user", "content": "What is it?"}]
    routing_data = await client.route_query("Do you know anything else about it?", recent_messages=history)
    
    assert routing_data["intent"] == "legal_rag"
    args, kwargs = mock_instance.aio.models.generate_content.call_args
    # Verify history was prepended
    assert "Recent Conversation History" in kwargs["contents"]
    assert "Assistant: The Civil Code" in kwargs["contents"]
    assert "Current Query: Do you know anything else about it?" in kwargs["contents"]

@pytest.mark.asyncio
@patch('app.services.llm_client.genai.Client')
async def test_ask_method(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.text = "This is the answer"
    mock_response.usage_metadata.candidates_token_count = 10
    mock_response.usage_metadata.prompt_token_count = 5
    mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    
    # Passing structured history
    history = [
        {"role": "user", "content": "Question 1"},
        {"role": "assistant", "content": "Answer 1"}
    ]
    
    result = await client.ask(
        question="Question 2",
        structured_history=history,
        context_markdown="Context",
        session_summary="Summary",
        is_conversational=False
    )
    
    assert result["answer"] == "This is the answer"
    assert result["model"] == "gemini-3.1"
    
    args, kwargs = mock_instance.aio.models.generate_content.call_args
    assert kwargs["model"] == "gemini-3.1"
    
    # Assert structured history was mapped correctly
    contents = kwargs["contents"]
    assert len(contents) == 3 # 2 history + 1 current question
    
    # In the new code, contents is a list of types.Content objects OR dicts
    first_role = contents[0].role if hasattr(contents[0], "role") else contents[0]["role"]
    assert first_role == "user"
    
    second_role = contents[1].role if hasattr(contents[1], "role") else contents[1]["role"]
    assert second_role == "model"
    
    third_role = contents[2].role if hasattr(contents[2], "role") else contents[2]["role"]
    assert third_role == "user"
    
    # Ensure context markdown is in the final message
    third_parts = contents[2].parts if hasattr(contents[2], "parts") else contents[2]["parts"]
    third_text = third_parts[0].text if hasattr(third_parts[0], "text") else third_parts[0]["text"]
    assert "Context" in third_text

@pytest.mark.asyncio
@patch('app.services.llm_client.genai.Client')
async def test_ask_conversational(mock_genai_client):
    mock_instance = mock_genai_client.return_value
    mock_response = MagicMock()
    mock_response.text = "Hello!"
    mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
    
    client = GeminiClient(main_model="gemini-3.1")
    
    result = await client.ask(
        question="Hello",
        structured_history=[],
        context_markdown="",
        is_conversational=True
    )
    
    assert result["answer"] == "Hello!"
