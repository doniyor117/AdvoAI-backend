"""
chat.py — RAG Chat API Route

Receives a user's question, triggers the full Vector Search
and Parent Document Retrieval pipeline, asks Gemini with
conversation context (rolling summary), and returns the
grounded answer.
"""

import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field

from app.services.rag_pipeline import retrieve_context
from app.services.llm_client import get_llm_client
from app.middleware import require_rate_limit, get_current_user
from app.database.queries import (
    get_session_by_id, update_session_summary,
    create_session, rename_session,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=2, description="The legal question to ask AdvoAI.")
    session_id: Optional[str] = Field(default=None, description="Chat session ID for conversation continuity.")
    top_k: int = Field(default=5, ge=1, le=10, description="Number of vector chunks to retrieve.")


@router.post("/")
async def ask_advoai(
    request: ChatRequest,
    http_request: Request,
    user: Optional[Dict[str, Any]] = Depends(require_rate_limit),
):
    """
    Core RAG chatbot endpoint with rolling summary memory.

    Flow:
    1. Check rate limits (guest/free/admin)
    2. Fetch existing rolling summary from session (if any)
    3. Run RAG retrieval pipeline
    4. Generate answer with Gemini (including conversation context)
    5. Generate updated rolling summary
    6. Save summary to DB
    7. Auto-generate session title on first message
    """
    try:
        logger.info(f"Processing chat request: {request.question[:50]}...")

        # ── 1. Resolve session & fetch rolling summary ───────
        session = None
        conversation_summary = ""

        if request.session_id and user:
            session = get_session_by_id(request.session_id)
            if session and session["user_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Access denied to this session.")
            if session:
                conversation_summary = session.get("rolling_summary", "")
        elif user and not request.session_id:
            # Auto-create a session for authenticated users
            session = create_session(user["id"])
            logger.info(f"Auto-created session: {session['id']}")

        # ── 2. RAG retrieval pipeline ────────────────────────
        rag_result = retrieve_context(
            question=request.question,
            top_k=request.top_k,
        )

        # Guard: no documents found
        if not rag_result["parent_documents"]:
            return {
                "answer": "I couldn't find any relevant legal documents in my database to answer this question. Please try rephrasing or asking about a different topic.",
                "sources": [],
                "citations": [],
                "session_id": session["id"] if session else None,
                "chunks_used": 0,
            }

        # ── 3. Generate answer with Gemini ───────────────────
        try:
            client = get_llm_client()
            llm_result = client.ask(
                question=rag_result["question"],
                context_markdown=rag_result["context_markdown"],
                conversation_summary=conversation_summary,
            )
        except (OSError, ConnectionError) as net_err:
            logger.error(f"LLM network error (DNS/connection): {net_err}")
            raise HTTPException(
                status_code=503,
                detail="Unable to reach the AI service. Please check your internet connection and try again."
            )

        # ── 4. Update rolling summary (async-friendly) ───────
        if session and user:
            try:
                updated_summary = client.summarize(
                    user_message=request.question,
                    ai_response=llm_result["answer"],
                    previous_summary=conversation_summary,
                )
                update_session_summary(session["id"], updated_summary)

                # Auto-title on first message (when summary was empty)
                if not conversation_summary:
                    try:
                        title = client.generate_title(request.question)
                        rename_session(session["id"], title)
                    except Exception as te:
                        logger.warning(f"Title generation failed: {te}")

            except Exception as se:
                logger.warning(f"Summary update failed (non-fatal): {se}")

        # ── 5. Format response for frontend ──────────────────
        # Citations = deduplicated parent documents (not individual chunks)
        safe_citations = []
        seen_doc_ids = set()
        for doc in rag_result["parent_documents"]:
            doc_id = doc.get("source_doc_id", doc["id"])
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            safe_citations.append({
                "id": doc_id,
                "title": doc.get("title", "Untitled Document"),
                "source_url": doc.get("source_url", "#"),
                "text": doc.get("full_markdown") or "",  # full content for the insight panel
            })

        return {
            "answer": llm_result["answer"],
            "model_used": llm_result["model"],
            "citations": safe_citations,
            "session_id": session["id"] if session else None,
            "metadata": {
                "context_length_chars": llm_result["context_length"],
                "prompt_length_chars": llm_result["prompt_length"],
                "chunks_used": len(rag_result["matched_chunks"]),
                "documents_used": len(rag_result["parent_documents"]),
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

