"""
admin.py — Admin Panel API Routes

Protected endpoints for application administration.
All routes require admin role.
"""

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from app.middleware import require_admin
from app.database.queries import (
    get_all_users, update_user_role, get_admin_stats, get_all_documents_admin,
    get_all_settings, update_setting,
    get_document_full, update_document_title, delete_document,
    toggle_ban_user, get_user_stats,
)
from app.ingestion.main_ingest import process_and_ingest_law

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request Models ───────────────────────────────────────────

class UpdateRoleRequest(BaseModel):
    role: str  # 'guest', 'free', 'admin'


class IngestRequest(BaseModel):
    url: str


class UpdateSettingsRequest(BaseModel):
    current_llm_model: Optional[str] = None
    guest_message_limit: Optional[int] = None
    free_daily_limit: Optional[int] = None


class UpdateDocTitleRequest(BaseModel):
    title: str


# ── Dashboard ────────────────────────────────────────────────

@router.get("/stats")
async def dashboard_stats(user=Depends(require_admin)):
    """Returns aggregated statistics for the admin dashboard."""
    stats = get_admin_stats()
    return {"stats": stats}


# ── System Settings ──────────────────────────────────────────

@router.get("/settings")
async def get_settings(user=Depends(require_admin)):
    """Returns all system settings."""
    settings = get_all_settings()
    return {"settings": settings}


@router.patch("/settings")
async def update_settings(request: UpdateSettingsRequest, user=Depends(require_admin)):
    """Updates system settings (partial update)."""
    updated = []
    if request.current_llm_model is not None:
        update_setting("current_llm_model", request.current_llm_model)
        updated.append("current_llm_model")
    if request.guest_message_limit is not None:
        update_setting("guest_message_limit", str(request.guest_message_limit))
        updated.append("guest_message_limit")
    if request.free_daily_limit is not None:
        update_setting("free_daily_limit", str(request.free_daily_limit))
        updated.append("free_daily_limit")

    return {"message": f"Updated: {', '.join(updated)}", "settings": get_all_settings()}


# ── Users ────────────────────────────────────────────────────

@router.get("/users")
async def list_users(user=Depends(require_admin)):
    """Lists all users with their usage data."""
    users = get_all_users()
    return {"users": users}


@router.patch("/users/{user_id}/role")
async def change_user_role(user_id: str, request: UpdateRoleRequest, user=Depends(require_admin)):
    """Changes a user's role."""
    if request.role not in ("guest", "free", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'guest', 'free', or 'admin'.")

    try:
        await update_user_role(user_id, request.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": f"User role updated to '{request.role}'."}


@router.patch("/users/{user_id}/ban")
async def ban_user(user_id: str, user=Depends(require_admin)):
    """Toggles the ban status of a user."""
    new_status = toggle_ban_user(user_id)
    action = "banned" if new_status else "unbanned"
    return {"message": f"User {action}.", "is_banned": new_status}


@router.get("/users/{user_id}/stats")
async def user_stats(user_id: str, user=Depends(require_admin)):
    """Returns usage statistics for a specific user."""
    stats = await get_user_stats(user_id)
    return {"stats": stats}


# ── Documents ────────────────────────────────────────────────

@router.get("/documents")
async def list_documents(user=Depends(require_admin)):
    """Lists all ingested documents with metadata."""
    documents = get_all_documents_admin()
    return {"documents": documents}


@router.get("/documents/{doc_id}")
async def view_document(doc_id: str, user=Depends(require_admin)):
    """Gets a single document with its full markdown content."""
    doc = get_document_full(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"document": doc}


@router.patch("/documents/{doc_id}")
async def edit_document(doc_id: str, request: UpdateDocTitleRequest, user=Depends(require_admin)):
    """Updates a document's title."""
    doc = get_document_full(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    update_document_title(doc_id, request.title)
    return {"message": "Document title updated.", "title": request.title}


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: str, user=Depends(require_admin)):
    """Deletes a document and all its associated chunks (CASCADE)."""
    doc = get_document_full(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    await delete_document(doc_id)
    logger.info(f"Admin deleted document {doc_id} ({doc['source_doc_id']})")
    return {"message": "Document and all associated chunks deleted."}


# ── Ingestion ────────────────────────────────────────────────

@router.post("/ingest")
async def trigger_ingest(request: IngestRequest, user=Depends(require_admin)):
    """
    Triggers document ingestion from a Lex.uz URL.
    This is a synchronous operation — the request will block until complete.
    """
    logger.info(f"Admin {user['email']} triggered ingestion for: {request.url}")

    try:
        result = await process_and_ingest_law(request.url)
        return {
            "status": "success",
            "message": "Document ingested successfully.",
            "data": result,
        }
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
