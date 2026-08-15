"""
sessions.py — Chat Session API Routes

CRUD operations for chat sessions, protected by authentication.
"""

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Any, List, Literal, Optional

from app.middleware import require_auth
from app.database.queries import (
    create_session, get_user_sessions, get_session_by_id,
    rename_session, toggle_pin_session, delete_session,
    get_session_messages, documents_not_owned_by, import_guest_session
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Guest sessions are bounded by what fits in localStorage; this is just a hard
# ceiling so an import can never become an unbounded transaction.
MAX_IMPORT_MESSAGES = 200


# ── Request Models ───────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=255)


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None
    toggle_pin: Optional[bool] = None


class ImportAttachment(BaseModel):
    document_id: str
    display_name: Optional[str] = None
    mime_type: Optional[str] = None
    s3_key: Optional[str] = None


class ImportMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=100_000)
    sources: Optional[Any] = None
    attachments: Optional[List[ImportAttachment]] = None


class ImportSessionRequest(BaseModel):
    messages: List[ImportMessage] = Field(min_length=1, max_length=MAX_IMPORT_MESSAGES)


# ── Routes ───────────────────────────────────────────────────

@router.get("")
async def list_sessions(user=Depends(require_auth)):
    """Lists all sessions for the authenticated user."""
    sessions = await get_user_sessions(user["id"])
    return {"sessions": sessions}


@router.post("")
async def new_session(request: CreateSessionRequest, user=Depends(require_auth)):
    """Creates a new chat session."""
    session = await create_session(user["id"], request.title)
    if not session:
        raise HTTPException(status_code=500, detail="Failed to create session.")
    return {"session": session}


@router.get("/{session_id}")
async def get_session(session_id: str, user=Depends(require_auth)):
    """Gets a specific session by ID."""
    session = await get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")
    return {"session": session}


@router.get("/{session_id}/messages")
async def get_messages(session_id: str, user=Depends(require_auth)):
    """Gets the message history for a specific session."""
    session = await get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")
    
    messages = await get_session_messages(session_id, limit=500)
    return {"messages": messages}


@router.patch("/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest, user=Depends(require_auth)):
    """Updates a session (rename or toggle pin)."""
    session = await get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")

    if request.title is not None:
        await rename_session(session_id, request.title)
    if request.toggle_pin:
        await toggle_pin_session(session_id)

    updated = await get_session_by_id(session_id)
    return {"session": updated}


@router.delete("/{session_id}")
async def remove_session(session_id: str, user=Depends(require_auth)):
    """Deletes a chat session."""
    session = await get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")

    await delete_session(session_id)
    return {"message": "Session deleted."}


@router.post("/import")
async def import_session(request: ImportSessionRequest, user=Depends(require_auth)):
    """Migrates a guest's local chat history into a persistent, owned session.

    `role` is already constrained to the values the DB CHECK constraint accepts by
    the request model, so a bad row is rejected before any write happens. Attachments
    are re-checked against ownership here — a client-supplied `document_id` for
    another user's document is dropped rather than imported, since session ownership
    is the only gate `build_parts` relies on to decide a history attachment is safe
    to hand back to Gemini.
    """
    all_doc_ids = list({
        att.document_id
        for m in request.messages
        for att in (m.attachments or [])
    })
    unreadable = set(await documents_not_owned_by(all_doc_ids, user["id"])) if all_doc_ids else set()

    messages = []
    for m in request.messages:
        kept_attachments = [
            att.model_dump() for att in (m.attachments or [])
            if att.document_id not in unreadable
        ]
        messages.append({
            "role": m.role,
            "content": m.text,
            "sources": m.sources,
            "attachments": kept_attachments or None,
        })

    first_user_text = next((m["content"] for m in messages if m["role"] == "user"), "Imported chat")
    title = first_user_text.strip()[:60] or "Imported chat"

    session = await import_guest_session(user["id"], title, messages)
    return {"session": session}
