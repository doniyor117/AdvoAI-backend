"""attachments.py — turning stored documents into Gemini prompt parts.

This is the single place that converts a persisted `uploaded_documents` row into
something the model can read. Both the current turn and every replayed history turn
go through `build_parts()`, which is what keeps a document in context for the whole
conversation instead of only the turn it was attached on.

Two document classes, deliberately handled differently:

  kind='text'   Converted to Markdown at upload time. The text lives in Postgres and is
                inlined directly into the prompt. It never touches the Gemini Files API,
                so it cannot expire (48h TTL) or become unreadable after an API-key
                rotation — the two ways a .docx used to die with a 403.

  kind='media'  PDFs and images, which Gemini must read natively. The Files handle is
                cached with its real expiry and re-minted from R2 on demand.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.genai import types

from app.database.queries import (
    get_uploaded_documents,
    update_document_gemini_handle,
)
from app.services.storage import download_file_from_s3

logger = logging.getLogger(__name__)

# Inlined text is capped so one enormous document cannot crowd out the rest of the
# conversation. Gemini's window is large, but replaying an unbounded document on
# every turn of a long chat is what makes costs run away.
MAX_INLINE_CHARS = 200_000

# Re-mint a Files handle slightly before it actually lapses.
_EXPIRY_SAFETY_MARGIN = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _handle_is_fresh(doc: Dict[str, Any]) -> bool:
    """True if the cached Gemini handle is present and not about to expire."""
    if not doc.get("gemini_uri") or not doc.get("gemini_name"):
        return False
    expires_at = doc.get("gemini_expires_at")
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at - _EXPIRY_SAFETY_MARGIN > _now()


def _expiry_of(uploaded_file: Any) -> datetime:
    """Reads the real expiry off the Files API response, falling back to 48h."""
    raw = getattr(uploaded_file, "expiration_time", None)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return _now() + timedelta(hours=48)


def _state_name(file: Any) -> str:
    """FileState enum -> bare name. str(FileState.ACTIVE) is 'FileState.ACTIVE'."""
    state = getattr(file, "state", None)
    if state is None:
        return "ACTIVE"
    return str(getattr(state, "name", state)).upper()


async def _await_active(client: Any, file: Any, max_wait_seconds: int = 30) -> Any:
    import asyncio
    waited = 0
    while _state_name(file) == "PROCESSING":
        if waited >= max_wait_seconds:
            raise RuntimeError(f"File {file.name} still PROCESSING after {max_wait_seconds}s")
        await asyncio.sleep(1)
        waited += 1
        file = await client.client.aio.files.get(name=file.name)
    if _state_name(file) == "FAILED":
        raise RuntimeError(f"File {file.name} failed to process")
    return file


async def ensure_live_gemini_file(client: Any, doc: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Returns a usable {uri, mime_type} for a media document, re-uploading if needed.

    Returns None when the document cannot be restored (no S3 copy, or the upload
    failed) so callers can degrade instead of handing Gemini a dead URI.
    """
    if _handle_is_fresh(doc):
        return {"uri": doc["gemini_uri"], "mime_type": doc.get("gemini_mime") or doc["original_mime"]}

    s3_key = doc.get("s3_key")
    if not s3_key:
        logger.warning(
            f"Document {doc['id']} ('{doc['display_name']}') has no S3 copy and its Gemini "
            f"handle has expired — it cannot be restored."
        )
        return None

    tmp_path = await download_file_from_s3(s3_key)
    if not tmp_path:
        logger.warning(f"Could not download {s3_key} from storage to restore document {doc['id']}")
        return None

    try:
        uploaded = await client.client.aio.files.upload(
            file=tmp_path,
            config={
                "display_name": doc["display_name"],
                "mime_type": doc["original_mime"],
            },
        )
        uploaded = await _await_active(client, uploaded)

        mime = uploaded.mime_type or doc["original_mime"]
        await update_document_gemini_handle(
            doc["id"], uploaded.uri, uploaded.name, mime, _expiry_of(uploaded)
        )
        logger.info(f"Re-minted Gemini handle for document {doc['id']} ('{doc['display_name']}')")
        return {"uri": uploaded.uri, "mime_type": mime}
    except Exception:
        logger.exception(f"Failed to restore document {doc['id']} to the Gemini Files API")
        return None
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _text_part(doc: Dict[str, Any]) -> types.Part:
    text = doc.get("extracted_text") or ""
    truncated = ""
    if len(text) > MAX_INLINE_CHARS:
        text = text[:MAX_INLINE_CHARS]
        truncated = "\n\n[...document truncated for length...]"
    return types.Part.from_text(
        text=f"### Attached document: {doc['display_name']}\n\n{text}{truncated}"
    )


async def build_parts(client: Any, doc_ids: List[str]) -> List[types.Part]:
    """Builds the Gemini parts for a set of stored documents.

    Used for BOTH the current turn and replayed history turns — that symmetry is the
    whole point: a document attached on turn 1 stays readable on turn 10.
    """
    if not doc_ids:
        return []

    docs = await get_uploaded_documents(doc_ids)
    parts: List[types.Part] = []

    for doc in docs:
        if doc["kind"] == "text":
            if doc.get("extracted_text"):
                parts.append(_text_part(doc))
            else:
                logger.warning(f"Text document {doc['id']} has no extracted_text; skipping")
            continue

        live = await ensure_live_gemini_file(client, doc)
        if live:
            parts.append(types.Part.from_uri(file_uri=live["uri"], mime_type=live["mime_type"]))
        else:
            # Better to tell the model the file is gone than to silently drop it and
            # have it claim the user never attached anything.
            parts.append(types.Part.from_text(
                text=f"[The user attached '{doc['display_name']}', but it is no longer "
                     f"available for analysis. Ask them to attach it again.]"
            ))

    return parts


def doc_ids_from_message(msg: Dict[str, Any]) -> List[str]:
    """Extracts document ids from a persisted message's `attachments` JSONB."""
    attachments = msg.get("attachments") or []
    if not isinstance(attachments, list):
        return []
    return [a["document_id"] for a in attachments
            if isinstance(a, dict) and a.get("document_id")]
