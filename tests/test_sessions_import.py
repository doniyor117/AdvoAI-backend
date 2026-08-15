import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.main import app
from app.middleware import require_auth


def override_require_auth():
    return {"id": "test_user_id"}


app.dependency_overrides[require_auth] = override_require_auth
client = TestClient(app)


@patch('app.routes.sessions.import_guest_session', new_callable=AsyncMock)
@patch('app.routes.sessions.documents_not_owned_by', new_callable=AsyncMock)
def test_import_drops_foreign_document(mock_not_owned, mock_import):
    # The user attached a document id they don't own; import must drop it, not 403 the
    # whole request or leak the document into the new session.
    mock_not_owned.return_value = ["foreign-doc-id"]
    mock_import.return_value = {"id": "new-session-id", "user_id": "test_user_id", "title": "Hi"}

    response = client.post(
        "/api/sessions/import",
        json={"messages": [
            {"role": "user", "text": "Hi", "attachments": [{"document_id": "foreign-doc-id"}]},
        ]},
    )

    assert response.status_code == 200
    imported_messages = mock_import.call_args[0][2]
    assert imported_messages[0]["attachments"] is None


@patch('app.routes.sessions.import_guest_session', new_callable=AsyncMock)
@patch('app.routes.sessions.documents_not_owned_by', new_callable=AsyncMock)
def test_import_rejects_invalid_role(mock_not_owned, mock_import):
    mock_not_owned.return_value = []

    response = client.post(
        "/api/sessions/import",
        json={"messages": [{"role": "model", "text": "not a valid role"}]},
    )

    assert response.status_code == 422
    mock_import.assert_not_called()


@patch('app.routes.sessions.import_guest_session', new_callable=AsyncMock)
@patch('app.routes.sessions.documents_not_owned_by', new_callable=AsyncMock)
def test_import_caps_message_count(mock_not_owned, mock_import):
    mock_not_owned.return_value = []

    response = client.post(
        "/api/sessions/import",
        json={"messages": [{"role": "user", "text": "x"} for _ in range(201)]},
    )

    assert response.status_code == 422
    mock_import.assert_not_called()
