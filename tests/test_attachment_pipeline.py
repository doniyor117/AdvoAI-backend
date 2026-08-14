"""Regression tests for the attachment pipeline.

Every test here corresponds to a defect that shipped and broke document uploads:
a .docx sent with a message failed with a generic 500, and follow-up turns lost
the document entirely.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from google.genai import types

from app.services.llm_client import GeminiClient, EmptyLLMResponse, _finish_reason
from app.routes.chat import _file_state_name


# ── FileState normalisation ──────────────────────────────────────────────
# The original check was `str(state).upper() in ("ACTIVE", "FILE_STATE_ACTIVE", "2")`.
# google-genai renders the enum as "FileState.ACTIVE", so .upper() gives
# "FILESTATE.ACTIVE" and the check was ALWAYS false: every upload polled for the
# full 30s timeout and then handed Gemini a file it never confirmed was ready.

def _file_with_state(state):
    return type("F", (), {"state": state, "name": "files/test"})()


def test_file_state_active_is_recognised():
    assert _file_state_name(_file_with_state(types.FileState.ACTIVE)) == "ACTIVE"


def test_file_state_processing_is_recognised():
    assert _file_state_name(_file_with_state(types.FileState.PROCESSING)) == "PROCESSING"


def test_file_state_failed_is_recognised():
    assert _file_state_name(_file_with_state(types.FileState.FAILED)) == "FAILED"


def test_missing_file_state_is_treated_as_ready():
    """Older API responses omit `state`; they must not spin in the poll loop."""
    assert _file_state_name(_file_with_state(None)) == "ACTIVE"


def test_raw_string_state_is_handled():
    assert _file_state_name(_file_with_state("ACTIVE")) == "ACTIVE"


# ── Retry classification ─────────────────────────────────────────────────
# A stale Gemini file URI returns 403 PERMISSION_DENIED. The old loop retried it
# 5 times with 1+2+4+8s of backoff — ~15s spent on an outcome already decided.

@pytest.mark.parametrize("err", [
    "403 PERMISSION_DENIED. You do not have permission to access the File x",
    "404 NOT_FOUND",
    "400 INVALID_ARGUMENT",
    "401 UNAUTHENTICATED",
])
def test_client_errors_are_not_retried(err):
    assert GeminiClient._is_retryable(err) is False


@pytest.mark.parametrize("err", [
    "429 RESOURCE_EXHAUSTED",
    "503 UNAVAILABLE",
    "500 INTERNAL",
    "Connection reset by peer",
])
def test_transient_errors_are_retried(err):
    assert GeminiClient._is_retryable(err) is True


@pytest.mark.asyncio
@patch('app.services.llm_client.api_key_manager')
async def test_non_retryable_error_fails_immediately(mock_mgr):
    """A 403 must raise on the first attempt, not after the full backoff ladder."""
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=Exception("403 PERMISSION_DENIED")
    )
    mock_mgr.get_current_client.return_value = mock_client
    mock_mgr.keys = ["k1"]

    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    with pytest.raises(Exception, match="PERMISSION_DENIED"):
        await client._generate_with_retry(model="m", contents=[], config=None)

    assert mock_client.aio.models.generate_content.await_count == 1


def _stub_router(mock_mgr, text: str):
    """Point the key manager at a client that returns `text` from generate_content."""
    resp = MagicMock()
    resp.candidates = None
    resp.text = text
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=resp)
    mock_mgr.get_current_client.return_value = mock_client
    mock_mgr.keys = ["k1"]


# ── route_query must not drop fields the router prompt asks for ──────────
# Dropping these made CONTRACT_TEMPLATES unreachable (get_template("") always hit
# its fallback) and made adaptive top-k inert.

@pytest.mark.asyncio
@patch('app.services.llm_client.api_key_manager')
async def test_route_query_preserves_contract_type(mock_mgr):
    _stub_router(mock_mgr, '{"intent": "create_contract", "contract_type": "NDA"}')

    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routed = await client.route_query("Draft me an NDA")

    assert routed["intent"] == "create_contract"
    assert routed["contract_type"] == "NDA"


@pytest.mark.asyncio
@patch('app.services.llm_client.api_key_manager')
async def test_route_query_preserves_ideal_top_k(mock_mgr):
    _stub_router(mock_mgr, '{"intent": "legal_rag", "search_query": "penalty", "ideal_top_k": 8}')

    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routed = await client.route_query("A complex multi-part question")

    assert routed["ideal_top_k"] == 8


@pytest.mark.asyncio
@patch('app.services.llm_client.api_key_manager')
async def test_route_query_clamps_out_of_range_top_k(mock_mgr):
    _stub_router(mock_mgr, '{"intent": "legal_rag", "search_query": "x", "ideal_top_k": 500}')

    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    routed = await client.route_query("q")

    assert routed["ideal_top_k"] == 10


# ── Empty LLM responses must be typed, not AttributeError ────────────────
# `response.text.strip()` raised "'NoneType' object has no attribute 'strip'"
# whenever the model returned no candidate, surfacing as an opaque 500.

@pytest.mark.asyncio
@patch('app.services.llm_client.api_key_manager')
async def test_empty_response_raises_typed_error_with_finish_reason(mock_mgr):
    resp = MagicMock()
    resp.text = None
    candidate = MagicMock()
    candidate.finish_reason = types.FinishReason.SAFETY
    resp.candidates = [candidate]

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=resp)
    mock_mgr.get_current_client.return_value = mock_client
    mock_mgr.keys = ["k1"]

    client = GeminiClient(main_model="gemini-3.1", router_model="gemma-4")
    with pytest.raises(EmptyLLMResponse) as exc:
        await client.ask(question="hi", context_markdown="ctx")

    assert exc.value.finish_reason == "SAFETY"


def test_finish_reason_survives_missing_candidates():
    resp = MagicMock()
    resp.candidates = []
    assert _finish_reason(resp) == "UNKNOWN"


# ── Document rehydration ─────────────────────────────────────────────────
# The headline bug: llm_client.ask() rebuilt history as text-only parts, so a
# document attached on turn 1 was invisible from turn 2 onward and the model
# asked the user to upload a file that was plainly visible in the chat.

from app.services.attachments import (
    build_parts, doc_ids_from_message, _handle_is_fresh, MAX_INLINE_CHARS,
)
from datetime import datetime, timedelta, timezone


def test_doc_ids_extracted_from_message_attachments():
    msg = {"attachments": [{"document_id": "a", "display_name": "x.docx"},
                           {"document_id": "b"}]}
    assert doc_ids_from_message(msg) == ["a", "b"]


def test_doc_ids_tolerates_legacy_attachments_without_document_id():
    """Rows written before the document store must not crash history rebuilding."""
    msg = {"attachments": [{"s3_key": "k", "display_name": "old.docx"}]}
    assert doc_ids_from_message(msg) == []


def test_doc_ids_tolerates_missing_or_malformed_attachments():
    assert doc_ids_from_message({}) == []
    assert doc_ids_from_message({"attachments": None}) == []
    assert doc_ids_from_message({"attachments": "not-a-list"}) == []


@pytest.mark.asyncio
@patch('app.services.attachments.get_uploaded_documents', new_callable=AsyncMock)
async def test_text_document_is_inlined_not_uploaded(mock_get_docs):
    """A converted .docx must never touch the Files API — that is what made it expire."""
    mock_get_docs.return_value = [{
        "id": "d1", "kind": "text", "display_name": "contract.docx",
        "original_mime": "text/markdown", "extracted_text": "Salary: 8,000,000",
        "s3_key": None, "gemini_uri": None, "gemini_name": None, "gemini_expires_at": None,
    }]
    client = MagicMock()

    parts = await build_parts(client, ["d1"])

    assert len(parts) == 1
    assert "Salary: 8,000,000" in parts[0].text
    assert "contract.docx" in parts[0].text
    client.client.aio.files.upload.assert_not_called()


@pytest.mark.asyncio
@patch('app.services.attachments.get_uploaded_documents', new_callable=AsyncMock)
async def test_oversized_text_document_is_truncated(mock_get_docs):
    mock_get_docs.return_value = [{
        "id": "d1", "kind": "text", "display_name": "big.docx",
        "original_mime": "text/markdown", "extracted_text": "x" * (MAX_INLINE_CHARS + 5000),
        "s3_key": None, "gemini_uri": None, "gemini_name": None, "gemini_expires_at": None,
    }]
    parts = await build_parts(MagicMock(), ["d1"])
    assert "truncated" in parts[0].text
    assert len(parts[0].text) < MAX_INLINE_CHARS + 500


@pytest.mark.asyncio
@patch('app.services.attachments.get_uploaded_documents', new_callable=AsyncMock)
async def test_unrestorable_media_tells_the_model_instead_of_vanishing(mock_get_docs):
    """Silently dropping the file is what made the model deny it was ever attached."""
    mock_get_docs.return_value = [{
        "id": "d1", "kind": "media", "display_name": "scan.png",
        "original_mime": "image/png", "extracted_text": None,
        "s3_key": None, "gemini_uri": None, "gemini_name": None, "gemini_expires_at": None,
    }]
    parts = await build_parts(MagicMock(), ["d1"])
    assert len(parts) == 1
    assert "scan.png" in parts[0].text
    assert "no longer available" in parts[0].text


def test_fresh_gemini_handle_is_reused():
    doc = {"gemini_uri": "u", "gemini_name": "files/x",
           "gemini_expires_at": datetime.now(timezone.utc) + timedelta(hours=10)}
    assert _handle_is_fresh(doc) is True


def test_expired_gemini_handle_is_rejected():
    doc = {"gemini_uri": "u", "gemini_name": "files/x",
           "gemini_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
    assert _handle_is_fresh(doc) is False


def test_handle_expiring_within_safety_margin_is_rejected():
    doc = {"gemini_uri": "u", "gemini_name": "files/x",
           "gemini_expires_at": datetime.now(timezone.utc) + timedelta(minutes=2)}
    assert _handle_is_fresh(doc) is False


def test_naive_expiry_timestamp_is_treated_as_utc():
    doc = {"gemini_uri": "u", "gemini_name": "files/x",
           "gemini_expires_at": datetime.utcnow() + timedelta(hours=10)}
    assert _handle_is_fresh(doc) is True


# ── MIME resolution ──────────────────────────────────────────────────────
# The client accepts a file on extension OR mime; the server only checked
# content_type. Browsers report "" or application/octet-stream for .docx on
# Linux and Android, so a valid DOCX was rejected with a message listing DOCX
# as an accepted type.

from app.routes.chat import _resolve_mime

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_octet_stream_docx_resolves_by_extension():
    assert _resolve_mime("application/octet-stream", "contract.docx") == DOCX


def test_empty_content_type_resolves_by_extension():
    assert _resolve_mime("", "contract.docx") == DOCX


def test_correct_content_type_is_respected():
    assert _resolve_mime(DOCX, "contract.docx") == DOCX


def test_content_type_with_charset_is_normalised():
    assert _resolve_mime("text/plain; charset=utf-8", "notes.txt") == "text/plain"


def test_unknown_extension_keeps_reported_mime_so_it_is_rejected():
    assert _resolve_mime("application/x-msdownload", "virus.exe") == "application/x-msdownload"


def test_uppercase_extension_is_handled():
    assert _resolve_mime("application/octet-stream", "CONTRACT.DOCX") == DOCX


# ── Pre-saved user turn must be rolled back on failure ───────────────────
# chat.py writes the user turn BEFORE generation so a mid-flight reload still shows
# it. Leaving it behind on failure meant every retry appended another copy of the
# same question. This exercises the path an HTTP-level 403 test cannot reach,
# because that rejection happens before the pre-save.

from app.routes.chat import _rollback_user_message


@pytest.mark.asyncio
@patch('app.routes.chat.delete_message', new_callable=AsyncMock)
async def test_rollback_deletes_the_presaved_turn(mock_delete):
    await _rollback_user_message("msg-123")
    mock_delete.assert_awaited_once_with("msg-123")


@pytest.mark.asyncio
@patch('app.routes.chat.delete_message', new_callable=AsyncMock)
async def test_rollback_is_a_noop_when_nothing_was_saved(mock_delete):
    await _rollback_user_message(None)
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
@patch('app.routes.chat.delete_message', new_callable=AsyncMock)
async def test_rollback_failure_never_masks_the_original_error(mock_delete):
    """The rollback runs inside an exception handler; if it raised, it would replace
    the real error the user needs to see."""
    mock_delete.side_effect = Exception("db gone")
    await _rollback_user_message("msg-123")  # must not raise


# ── Guests must be refused, not crashed ──────────────────────────────────
# require_rate_limit yields None for guests, but both document endpoints write into
# a chat session and chat_sessions.user_id is NOT NULL — user["id"] on None raises
# TypeError and surfaces as an opaque 500.

from fastapi import HTTPException
from app.routes.documents import _require_user


def test_guest_is_refused_with_401():
    with pytest.raises(HTTPException) as exc:
        _require_user(None)
    assert exc.value.status_code == 401


def test_authenticated_user_passes_through():
    user = {"id": "u1"}
    assert _require_user(user) is user
