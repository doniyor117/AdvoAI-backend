"""
sessions.py — Chat Session API Routes

CRUD operations for chat sessions, protected by authentication.
"""

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional

from app.middleware import require_auth
from app.database.queries import (
    create_session, get_user_sessions, get_session_by_id,
    rename_session, toggle_pin_session, delete_session
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request Models ───────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=255)


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None
    toggle_pin: Optional[bool] = None


# ── Routes ───────────────────────────────────────────────────

@router.get("")
def list_sessions(user=Depends(require_auth)):
    """Lists all sessions for the authenticated user."""
    sessions = get_user_sessions(user["id"])
    return {"sessions": sessions}


@router.post("")
def new_session(request: CreateSessionRequest, user=Depends(require_auth)):
    """Creates a new chat session."""
    session = create_session(user["id"], request.title)
    if not session:
        raise HTTPException(status_code=500, detail="Failed to create session.")
    return {"session": session}


@router.get("/{session_id}")
def get_session(session_id: str, user=Depends(require_auth)):
    """Gets a specific session by ID."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")
    return {"session": session}


@router.patch("/{session_id}")
def update_session(session_id: str, request: UpdateSessionRequest, user=Depends(require_auth)):
    """Updates a session (rename or toggle pin)."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")

    if request.title is not None:
        rename_session(session_id, request.title)
    if request.toggle_pin:
        toggle_pin_session(session_id)

    updated = get_session_by_id(session_id)
    return {"session": updated}


@router.delete("/{session_id}")
def remove_session(session_id: str, user=Depends(require_auth)):
    """Deletes a chat session."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")

    delete_session(session_id)
    return {"message": "Session deleted."}
