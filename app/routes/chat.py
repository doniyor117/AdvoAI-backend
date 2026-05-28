"""
chat.py — RAG Chat API Route

Receives a user's question, determines intent (conversational vs RAG),
runs the pipeline, maintains a Hybrid Sliding Window chat history,
and returns the grounded answer.
"""

import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks
from pydantic import BaseModel, Field

from app.services.rag_pipeline import retrieve_context
from app.services.llm_client import get_llm_client
from app.middleware import require_rate_limit, get_current_user
from app.database.queries import (
    get_session_by_id, update_session_summary,
    create_session, rename_session,
    insert_message, get_session_messages, delete_oldest_message,
    log_router_analytics
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=2, description="The legal question to ask AdvoAI.")
    session_id: Optional[str] = Field(default=None, description="Chat session ID for conversation continuity.")
    top_k: int = Field(default=5, description="Number of chunks to retrieve.")

async def _background_history_maintenance(
    session_id: str,
    question: str,
    client: Any,
    recent_messages: list,
    session_summary: str
):
    """
    Runs in the background to summarize the sliding window and generate session titles,
    preventing long blocking API responses.
    """
    try:
        # Check sliding window size
        MAX_WINDOW_MESSAGES = 6
        current_messages = await get_session_messages(session_id, limit=100)
        
        if len(current_messages) > MAX_WINDOW_MESSAGES:
            dropped_messages = []
            while len(current_messages) > MAX_WINDOW_MESSAGES:
                oldest = await delete_oldest_message(session_id)
                if oldest:
                    dropped_messages.append(oldest)
                    current_messages.pop(0)
            
            if dropped_messages:
                dropped_text = "\n\n".join(
                    f"{msg['role'].capitalize()}: {msg['content']}" 
                    for msg in dropped_messages
                )
                new_summary = await client.summarize_archive(
                    old_messages=dropped_text,
                    previous_summary=session_summary
                )
                await update_session_summary(session_id, new_summary)

        # Auto-title on first message
        if not recent_messages:  # If it was empty before this query
            try:
                title = await client.generate_title(question)
                await rename_session(session_id, title)
            except Exception as te:
                logger.warning(f"Title generation failed: {te}")

    except Exception as se:
        logger.warning(f"History update background task failed: {se}")

@router.post("/", response_model=Dict[str, Any])
async def ask_advoai(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_rate_limit)
):
    """
    Core RAG chatbot endpoint with Hybrid History.
    """
    try:
        logger.info(f"Processing chat request: {request.question[:50]}...")

        client = await get_llm_client()

        # ── 1. Resolve session & fetch hybrid history ───────
        session = None
        session_summary = ""
        recent_messages = []

        if request.session_id and user:
            session = await get_session_by_id(request.session_id)
            if session and session["user_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Access denied to this session.")
        elif user and not request.session_id:
            session = await create_session(user["id"])
            logger.info(f"Auto-created session: {session['id']}")

        session_id = session["id"] if session else None

        if session_id:
            session_summary = session.get("session_summary", "")
            recent_messages = await get_session_messages(session_id, limit=5)

        # ── 2. Intent Routing ───────────────────────────────
        routing_data = await client.route_query(request.question, recent_messages=recent_messages if session_id else None)
        intent = routing_data.get("intent", "legal_rag")
        search_query = routing_data.get("search_query", request.question)
        ideal_top_k = routing_data.get("ideal_top_k", request.top_k)
        
        is_conversational = (intent == "conversational")
        logger.info(f"Query routed as: {intent} | Optimized Search Query: {search_query} | Ideal Top-K: {ideal_top_k}")
        
        # Log analytics in the background to reduce response latency
        background_tasks.add_task(
            log_router_analytics,
            session_id,
            request.question,
            intent,
            search_query,
            ideal_top_k
        )

        # ── 3. RAG retrieval pipeline (if needed) ───────────
        rag_result = {}
        if not is_conversational:
            rag_result = await retrieve_context(
                question=search_query,
                top_k=ideal_top_k,
            )

            # Guard: no documents found
            if not rag_result.get("parent_documents"):
                return {
                    "answer": "I couldn't find any relevant legal documents in my database to answer this question. Please try rephrasing or asking about a different topic.",
                    "sources": [],
                    "citations": [],
                    "session_id": session_id,
                    "chunks_used": 0,
                    "intent": intent,
                }

        # ── 4. Generate answer with Main LLM ────────────────
        try:
            llm_result = await client.ask(
                question=request.question,
                structured_history=recent_messages,
                context_markdown=rag_result.get("context_markdown", ""),
                session_summary=session_summary,
                is_conversational=is_conversational,
            )
        except (OSError, ConnectionError) as net_err:
            logger.error(f"LLM network error (DNS/connection): {net_err}")
            raise HTTPException(
                status_code=503,
                detail="Unable to reach the AI service. Please check your internet connection and try again."
            )

        # ── 5. Update Hybrid History (Archive Shift) ────────
        if session_id:
            try:
                # Save the new exchange to sliding window
                await insert_message(session_id, "user", request.question)
                await insert_message(session_id, "assistant", llm_result["answer"])

                # Check sliding window size and summarize in background
                background_tasks.add_task(
                    _background_history_maintenance,
                    session_id=session_id,
                    question=request.question,
                    client=client,
                    recent_messages=recent_messages,
                    session_summary=session_summary
                )

            except Exception as se:
                logger.warning(f"Message insert failed (non-fatal): {se}")

        # ── 6. Format response for frontend ─────────────────
        safe_citations = []
        if not is_conversational:
            seen_doc_ids = set()
            for doc in rag_result.get("parent_documents", []):
                # doc contains 'source_doc_id', 'title', 'root_title', 'full_markdown'
                doc_id = doc.get("source_doc_id")
                if doc_id in seen_doc_ids:
                    continue
                seen_doc_ids.add(doc_id)
                safe_citations.append({
                    "id": doc_id,
                    "title": doc.get("root_title", "Untitled Document"),
                    "source_url": doc.get("source_url", "#"),
                    "text": doc.get("full_markdown") or "", 
                })

        return {
            "answer": llm_result["answer"],
            "model_used": llm_result["model"],
            "citations": safe_citations,
            "session_id": session_id,
            "intent": intent,
            "metadata": {
                "context_length_chars": llm_result["context_length"],
                "prompt_length_chars": llm_result["prompt_length"],
                "chunks_used": len(rag_result.get("matched_chunks", [])),
                "documents_used": len(rag_result.get("parent_documents", [])),
            },
        }

    except HTTPException:
        raise
    except ValueError as ve:
        logger.error(f"Configuration error: {ve}")
        raise HTTPException(status_code=500, detail="Server configuration error. Ensure API keys are set.")
    except Exception as e:
        logger.error(f"Chat API error: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while processing your request.")
