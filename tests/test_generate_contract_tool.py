from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from app.main import app
from app.middleware import require_rate_limit


def override_require_rate_limit():
    return {"id": "test_user_id"}


app.dependency_overrides[require_rate_limit] = override_require_rate_limit
client = TestClient(app)


@patch('app.routes.chat.get_llm_client', new_callable=AsyncMock)
@patch('app.routes.chat.create_session', new_callable=AsyncMock)
@patch('app.routes.chat.get_session_messages', new_callable=AsyncMock)
@patch('app.database.queries.get_setting', new_callable=AsyncMock)
@patch('app.routes.chat.log_router_analytics', new_callable=AsyncMock)
@patch('app.routes.chat._run_generate_contract_tool', new_callable=AsyncMock)
def test_document_request_executes_tool_call_and_returns_file(
    mock_run_tool, mock_log, mock_get_setting, mock_get_messages, mock_create_session, mock_get_llm,
):
    mock_create_session.return_value = {"id": "session_new", "user_id": "test_user_id"}
    mock_get_messages.return_value = []

    mock_llm_instance = AsyncMock()
    mock_llm_instance.route_query.return_value = {"intent": "document_request"}
    mock_llm_instance.ask.return_value = {
        "answer": "",
        "tool_call": {"name": "generate_contract", "args": {"contract_type": "NDA"}},
        "model": "gemini-3.1",
        "context_length": 0,
        "prompt_length": 10,
    }
    mock_get_llm.return_value = mock_llm_instance

    mock_run_tool.return_value = {
        "chat_note": "Here is your NDA draft.",
        "attachment": {
            "document_id": "doc-1",
            "display_name": "NDA.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "s3_key": "key-1",
        },
    }

    response = client.post(
        "/api/chat/",
        json={"question": "Draft me an NDA"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Here is your NDA draft."
    assert data["intent"] == "document_request"
    assert data["attachments"] == [mock_run_tool.return_value["attachment"]]
    mock_run_tool.assert_awaited_once()


@patch('app.routes.chat.get_llm_client', new_callable=AsyncMock)
@patch('app.routes.chat.create_session', new_callable=AsyncMock)
@patch('app.routes.chat.get_session_messages', new_callable=AsyncMock)
@patch('app.database.queries.get_setting', new_callable=AsyncMock)
@patch('app.routes.chat.log_router_analytics', new_callable=AsyncMock)
@patch('app.routes.chat._run_generate_contract_tool', new_callable=AsyncMock)
def test_document_request_tool_failure_degrades_gracefully(
    mock_run_tool, mock_log, mock_get_setting, mock_get_messages, mock_create_session, mock_get_llm,
):
    mock_create_session.return_value = {"id": "session_new", "user_id": "test_user_id"}
    mock_get_messages.return_value = []

    mock_llm_instance = AsyncMock()
    mock_llm_instance.route_query.return_value = {"intent": "document_request"}
    mock_llm_instance.ask.return_value = {
        "answer": "",
        "tool_call": {"name": "generate_contract", "args": {"contract_type": "NDA"}},
        "model": "gemini-3.1",
        "context_length": 0,
        "prompt_length": 10,
    }
    mock_get_llm.return_value = mock_llm_instance
    mock_run_tool.side_effect = RuntimeError("storage down")

    response = client.post(
        "/api/chat/",
        json={"question": "Draft me an NDA"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["attachments"] == []
    assert "couldn't generate" in data["answer"].lower()
