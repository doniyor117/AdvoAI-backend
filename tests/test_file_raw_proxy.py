import os
import tempfile

from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.main import app

client = TestClient(app)


def test_raw_proxy_requires_auth():
    with patch('app.routes.chat.get_current_user', new_callable=AsyncMock) as mock_user:
        mock_user.return_value = None
        response = client.get("/api/chat/file/some-key.docx/raw")
    assert response.status_code == 401


def test_raw_proxy_streams_file_bytes():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    tmp.write(b"fake docx bytes")
    tmp.close()

    with patch('app.routes.chat.get_current_user', new_callable=AsyncMock) as mock_user, \
         patch('app.routes.chat.download_file_from_s3', new_callable=AsyncMock) as mock_download:
        mock_user.return_value = {"id": "u1"}
        mock_download.return_value = tmp.name

        response = client.get("/api/chat/file/abc123_report.docx/raw")

    assert response.status_code == 200
    assert response.content == b"fake docx bytes"
    # The proxy exists specifically so the browser's fetch()/blob path (docx
    # preview, forced download) works without needing R2 bucket CORS — a
    # same-origin-ish response is the whole point, so a real body is required.
    assert not os.path.exists(tmp.name)  # cleaned up by the background task
