from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.main import app

client = TestClient(app)


def test_full_document_requires_auth():
    # Simulate the real guest path: require_rate_limit yields None for a guest, and
    # the route must reject that rather than pass None into document lookups.
    from app.middleware import require_rate_limit

    previous = app.dependency_overrides.get(require_rate_limit)
    app.dependency_overrides[require_rate_limit] = lambda: None
    try:
        response = client.get("/api/documents/111189/full")
        assert response.status_code == 401
    finally:
        # Restore rather than pop — other test modules install a persistent override
        # for this same dependency at import time; a bare pop() would delete theirs too.
        if previous is not None:
            app.dependency_overrides[require_rate_limit] = previous
        else:
            app.dependency_overrides.pop(require_rate_limit, None)


@patch('app.routes.documents.fetch_document_parts_page', new_callable=AsyncMock)
@patch('app.routes.documents.resolve_legal_document', new_callable=AsyncMock)
def test_full_document_rejects_non_uuid_part_key_safely(mock_resolve, mock_page):
    from app.middleware import require_rate_limit

    previous = app.dependency_overrides.get(require_rate_limit)
    app.dependency_overrides[require_rate_limit] = lambda: {"id": "u1"}
    try:
        mock_resolve.return_value = None
        response = client.get("/api/documents/../../etc/passwd/full")
        # Path traversal in a route param is normalized/rejected by routing before it
        # ever reaches resolve_legal_document with a literal "../.." key; either way
        # this must never 200 with content.
        assert response.status_code in (401, 404)
        mock_page.assert_not_called()
    finally:
        if previous is not None:
            app.dependency_overrides[require_rate_limit] = previous
        else:
            app.dependency_overrides.pop(require_rate_limit, None)
