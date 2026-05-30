"""
admin.py — Admin Panel API Routes

Protected endpoints for application administration.
All routes require admin role.
"""

import logging

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Response, Request
from pydantic import BaseModel
from typing import Optional, List

from app.middleware import require_admin, require_root_admin
from app.routes.auth import _hash_password, _verify_password
from app.database.connection import get_connection

from app.database.queries import (
    get_all_users, update_user_role, get_admin_stats, get_all_documents_admin,
    get_all_settings, update_setting,
    get_document_full, delete_document,
    toggle_ban_user, get_user_stats, get_time_series_stats, get_router_analytics_stats,
    create_ingestion_jobs, update_ingestion_job_status, get_ingestion_jobs,
    get_all_sessions_audited, get_session_messages_audited,
    get_avg_response_times, delete_user
)
from app.ingestion.main_ingest import process_and_ingest_law
from app.services.rag_pipeline import retrieve_context

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request Models ───────────────────────────────────────────

class RAGTestRequest(BaseModel):
    query: str
    top_k: int = 5

class UpdateRoleRequest(BaseModel):
    role: str  # 'guest', 'free', 'admin'
    admin_password: Optional[str] = None

class VerifyAdminPasswordRequest(BaseModel):
    admin_password: str

class ChangeAdminPasswordRequest(BaseModel):
    current_password: str
    new_password: str

class IngestRequest(BaseModel):
    urls: List[str]
    category: str = "General"


class UpdateSettingsRequest(BaseModel):
    current_llm_model: Optional[str] = None
    current_router_model: Optional[str] = None
    guest_message_limit: Optional[int] = None
    free_daily_limit: Optional[int] = None
    free_daily_doc_limit: Optional[int] = None
    free_daily_image_limit: Optional[int] = None
    guest_doc_limit: Optional[int] = None
    guest_image_limit: Optional[int] = None
    custom_main_prompt: Optional[str] = None
    custom_router_prompt: Optional[str] = None
    override_prompts_enabled: Optional[bool] = None
    rag_top_k: Optional[int] = None
    override_rag_top_k: Optional[bool] = None
    custom_api_keys: Optional[str] = None
    global_notification: Optional[str] = None
    global_notification_type: Optional[str] = None


class UpdateDocumentMetadataRequest(BaseModel):
    title: str
    category: str
    act_type: str


# ── Dashboard ────────────────────────────────────────────────

@router.get("/stats")
async def dashboard_stats(user=Depends(require_admin)):
    """Returns aggregated statistics for the admin dashboard."""
    stats = await get_admin_stats()
    return {"stats": stats}


@router.get("/stats/charts")
async def dashboard_charts(range: str = "30d", user=Depends(require_admin)):
    """Returns time-series and router analytics for charts."""
    time_series = await get_time_series_stats(range_val=range)
    router_stats = await get_router_analytics_stats(range_val=range)
    return {
        "time_series": time_series["data"],
        "router_analytics": router_stats
    }


@router.get("/stats/performance")
async def dashboard_performance(range: str = "30d", user=Depends(require_admin)):
    """Returns average response times for router and LLM."""
    times = await get_avg_response_times(range_val=range)
    return {"performance": times}


# ── System Settings ──────────────────────────────────────────

@router.get("/settings")
async def get_settings(user=Depends(require_admin)):
    """Returns all system settings."""
    settings = await get_all_settings()
    return {"settings": settings}


@router.patch("/settings")
async def update_settings(request: UpdateSettingsRequest, user=Depends(require_admin)):
    """Updates system settings (partial update). Some fields require root_admin."""
    is_root = user.get("role") == "root_admin"
    updated = []

    # ── Root-admin-only fields ───────────────────────────────
    root_only_requested = []
    if request.current_llm_model is not None:
        root_only_requested.append("current_llm_model")
    if request.current_router_model is not None:
        root_only_requested.append("current_router_model")
    if request.custom_api_keys is not None:
        root_only_requested.append("custom_api_keys")

    if root_only_requested and not is_root:
        raise HTTPException(
            status_code=403,
            detail=f"Root admin required to modify: {', '.join(root_only_requested)}."
        )

    if request.current_llm_model is not None:
        await update_setting("current_llm_model", request.current_llm_model)
        updated.append("current_llm_model")
    if request.current_router_model is not None:
        await update_setting("current_router_model", request.current_router_model)
        updated.append("current_router_model")
    if request.custom_api_keys is not None:
        await update_setting("custom_api_keys", request.custom_api_keys)
        updated.append("custom_api_keys")
        from app.services.api_keys import api_key_manager
        await api_key_manager.async_reload_keys()

    # ── Admin-accessible fields ──────────────────────────────
    if request.guest_message_limit is not None:
        await update_setting("guest_message_limit", str(request.guest_message_limit))
        updated.append("guest_message_limit")
    if request.free_daily_limit is not None:
        await update_setting("free_daily_limit", str(request.free_daily_limit))
        updated.append("free_daily_limit")
    if request.free_daily_doc_limit is not None:
        await update_setting("free_daily_doc_limit", str(request.free_daily_doc_limit))
        updated.append("free_daily_doc_limit")
    if request.free_daily_image_limit is not None:
        await update_setting("free_daily_image_limit", str(request.free_daily_image_limit))
        updated.append("free_daily_image_limit")
    if request.guest_doc_limit is not None:
        await update_setting("guest_doc_limit", str(request.guest_doc_limit))
        updated.append("guest_doc_limit")
    if request.guest_image_limit is not None:
        await update_setting("guest_image_limit", str(request.guest_image_limit))
        updated.append("guest_image_limit")
    if request.custom_main_prompt is not None:
        await update_setting("custom_main_prompt", request.custom_main_prompt)
        updated.append("custom_main_prompt")
    if request.custom_router_prompt is not None:
        await update_setting("custom_router_prompt", request.custom_router_prompt)
        updated.append("custom_router_prompt")
    if request.override_prompts_enabled is not None:
        await update_setting("override_prompts_enabled", "true" if request.override_prompts_enabled else "false")
        updated.append("override_prompts_enabled")
    if request.rag_top_k is not None:
        await update_setting("rag_top_k", str(request.rag_top_k))
        updated.append("rag_top_k")
    if request.override_rag_top_k is not None:
        await update_setting("override_rag_top_k", "true" if request.override_rag_top_k else "false")
        updated.append("override_rag_top_k")
    # custom_api_keys is handled above in root-only section
    if request.global_notification is not None:
        await update_setting("global_notification", request.global_notification)
        updated.append("global_notification")
    if request.global_notification_type is not None:
        await update_setting("global_notification_type", request.global_notification_type)
        updated.append("global_notification_type")

    return {"message": f"Updated: {', '.join(updated)}", "settings": await get_all_settings()}


# ── Users ────────────────────────────────────────────────────

@router.get("/users")
async def list_users(user=Depends(require_admin)):
    """Lists all users with their usage data."""
    users = await get_all_users()
    return {"users": users}


@router.patch("/users/{user_id}/role")
async def change_user_role(user_id: str, request: UpdateRoleRequest, user=Depends(require_admin)):
    """Changes a user's role. Promoting to admin requires root_admin. Cannot modify root_admin accounts."""
    if request.role not in ("guest", "free", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'guest', 'free', or 'admin'.")

    # Look up the target user to check their current role
    from app.database.queries import get_user_by_id
    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Block anyone from touching a root_admin account via API
    if target_user.get("role") == "root_admin":
        raise HTTPException(status_code=403, detail="Root admin accounts cannot be modified via the API.")

    # Only root_admin can promote a user to admin
    if request.role == "admin" and user.get("role") != "root_admin":
        raise HTTPException(status_code=403, detail="Only root admins can promote users to admin.")

    # Prevent a regular admin from demoting another admin (privilege confusion)
    if target_user.get("role") == "admin" and user.get("role") != "root_admin":
        raise HTTPException(status_code=403, detail="Only root admins can modify admin accounts.")

    try:
        admin_password_hash = _hash_password(request.admin_password) if request.admin_password else None
        await update_user_role(user_id, request.role, admin_password_hash)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"message": f"User role updated to '{request.role}'."}

@router.patch("/password")
async def update_admin_password(request: ChangeAdminPasswordRequest, user=Depends(require_admin)):
    """Updates the admin password securely."""
    async with get_connection() as cur:
        await cur.execute("SELECT admin_password_hash FROM users WHERE id = %s", (user["id"],))
        row = cur.fetchone()

    # Verify current password
    if row and row["admin_password_hash"]:
        if not _verify_password(request.current_password, row["admin_password_hash"]):
            raise HTTPException(status_code=401, detail="Incorrect current admin password")

    new_hash = _hash_password(request.new_password)
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE users SET admin_password_hash = %s WHERE id = %s",
            (new_hash, user["id"])
        )

    return {"message": "Admin password updated successfully."}

@router.post("/verify-admin-password")
async def verify_admin_password(request: VerifyAdminPasswordRequest, response: Response, user=Depends(require_admin)):
    """Verifies the admin password and sets an unlock cookie."""
    async with get_connection() as cur:
        await cur.execute("SELECT admin_password_hash FROM users WHERE id = %s", (user["id"],))
        row = cur.fetchone()
        
    if not row or not row["admin_password_hash"]:
        # If no password is set, we can just allow them
        pass
    elif not _verify_password(request.admin_password, row["admin_password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect admin password")
        
    return {"message": "Admin password verified."}

@router.get("/check-lock")
async def check_admin_lock(request: Request, user=Depends(require_admin)):
    """We now use sessionStorage for auth locking, so the API always returns unlocked if the JWT is valid."""
    return {"unlocked": True}


@router.get("/sessions")
async def admin_get_sessions(limit: int = 50, offset: int = 0, user=Depends(require_admin)):
    """Returns a list of sessions including privacy status."""
    try:
        sessions = await get_all_sessions_audited(limit, offset)
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/messages")
async def admin_get_session_messages(session_id: str, user=Depends(require_admin)):
    """Returns session messages, redacted if user opted out."""
    try:
        messages = await get_session_messages_audited(session_id)
        return {"messages": messages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{user_id}/ban")
async def ban_user(user_id: str, user=Depends(require_admin)):
    """Toggles the ban status of a user. Cannot ban root_admin accounts."""
    from app.database.queries import get_user_by_id
    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")
    if target_user.get("role") == "root_admin":
        raise HTTPException(status_code=403, detail="Root admin accounts cannot be banned.")
    new_status = await toggle_ban_user(user_id)
    action = "banned" if new_status else "unbanned"
    return {"message": f"User {action}.", "is_banned": new_status}


@router.delete("/users/{user_id}")
async def remove_user(user_id: str, user=Depends(require_root_admin)):
    """Permanently deletes a user and all their data. Root admin only."""
    from app.database.queries import get_user_by_id
    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")
    if target_user.get("role") == "root_admin":
        raise HTTPException(status_code=403, detail="Root admin accounts cannot be deleted via the API.")
    # Prevent self-deletion
    if target_user["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    deleted = await delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    logger.info(f"Root admin {user['email']} permanently deleted user {target_user['email']} ({user_id})")
    return {"message": f"User '{target_user['email']}' permanently deleted."}


@router.post("/rag-test", dependencies=[Depends(require_admin)])
async def test_rag_retrieval(request: RAGTestRequest):
    """
    Tests the RAG pipeline by returning retrieved chunks and similarity scores without hitting the LLM.
    """
    try:
        result = await retrieve_context(request.query, top_k=request.top_k)
        # Format the result to return relevant data for the playground
        return {
            "query": result["question"],
            "matched_chunks": [
                {
                    "id": chunk.get("id"),
                    "document_part_id": chunk.get("document_part_id"),
                    "text": chunk.get("text"),
                    "similarity": chunk.get("similarity"),
                }
                for chunk in result["matched_chunks"]
            ],
            "parent_documents_count": len(result["parent_documents"]),
        }
    except Exception as e:
        logger.error(f"RAG test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/stats")
async def user_stats_admin(user_id: str, user=Depends(require_admin)):
    stats = await get_user_stats(user_id)
    if not stats:
        raise HTTPException(status_code=404, detail="User not found")
    return {"stats": stats}


@router.get("/users/{user_id}/charts")
async def user_charts_admin(user_id: str, range: str = "30d", user=Depends(require_admin)):
    """Returns time-series analytics for a specific user."""
    time_series = await get_time_series_stats(range_val=range, target_user_id=user_id)
    return {"time_series": time_series["data"]}


# ── Documents ────────────────────────────────────────────────

@router.get("/documents")
async def list_documents(user=Depends(require_admin)):
    """Lists all ingested documents with metadata."""
    documents = await get_all_documents_admin()
    return {"documents": documents}


@router.get("/documents/{doc_id}")
async def view_document(doc_id: str, user=Depends(require_admin)):
    """Gets a single document with its full markdown content."""
    doc = await get_document_full(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"document": doc}


@router.patch("/documents/{doc_id}")
async def edit_document(doc_id: str, request: UpdateDocumentMetadataRequest, user=Depends(require_admin)):
    """Updates a document's metadata."""
    from app.database.queries import update_document_metadata
    doc = await get_document_full(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    await update_document_metadata(doc_id, request.title, request.category, request.act_type)
    return {"message": "Document metadata updated.", "title": request.title}


@router.delete("/documents/{doc_id}")
async def remove_document(doc_id: str, user=Depends(require_root_admin)):
    """Deletes a document and all its associated chunks (CASCADE). Root admin only."""
    doc = await get_document_full(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    await delete_document(doc_id)
    logger.info(f"Root admin {user['email']} deleted document {doc_id} ({doc['source_doc_id']})")
    return {"message": "Document and all associated chunks deleted."}


# ── Bulk Ingestion ─────────────────────────────────────────────

async def process_ingestion_job(job_id: str, url: str, category: str):
    """Background worker for ingestion jobs."""
    try:
        await update_ingestion_job_status(job_id, 'processing')
        await process_and_ingest_law(url, category=category)
        await update_ingestion_job_status(job_id, 'completed')
    except Exception as e:
        logger.error(f"Background Ingestion failed for {url}: {e}")
        await update_ingestion_job_status(job_id, 'failed', str(e))

@router.post("/ingest")
async def trigger_ingest(request: IngestRequest, background_tasks: BackgroundTasks, user=Depends(require_root_admin)):
    """
    Triggers document ingestion from an array of Lex.uz URLs.
    This runs asynchronously and returns the created jobs. Root admin only.
    """
    logger.info(f"Admin {user['email']} triggered bulk ingestion for: {request.urls}")
    
    if not request.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    try:
        jobs = await create_ingestion_jobs(request.urls)
        for job in jobs:
            background_tasks.add_task(process_ingestion_job, job["id"], job["url"], request.category)
            
        return {
            "status": "success",
            "message": f"Started {len(jobs)} ingestion jobs in the background.",
            "jobs": jobs,
        }
    except Exception as e:
        logger.error(f"Bulk Ingestion creation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion setup failed: {str(e)}")

@router.get("/ingestion-jobs")
async def fetch_ingestion_jobs(user=Depends(require_admin)):
    """Fetches the latest ingestion jobs for the progress table."""
    jobs = await get_ingestion_jobs()
    return {"jobs": jobs}
