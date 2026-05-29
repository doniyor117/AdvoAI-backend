"""
chat.py — RAG Chat API Route

Receives a user's question, determines intent (conversational vs RAG),
runs the pipeline, maintains a Hybrid Sliding Window chat history,
and returns the grounded answer.
"""

import logging
import os
import tempfile
import asyncio
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks, UploadFile, File
from pydantic import BaseModel, Field

from app.services.rag_pipeline import retrieve_context
from app.services.llm_client import get_llm_client
from app.services.converter import (
    CONVERTIBLE_MIME_TYPES,
    convert_to_markdown,
    save_markdown_as_tempfile,
)
from app.services.rate_limiter import check_upload_limit
from app.services.storage import upload_file_to_s3, download_file_from_s3
from app.middleware import require_rate_limit, get_current_user
from app.database.queries import (
    get_session_by_id, update_session_summary,
    create_session, rename_session,
    insert_message, get_session_messages, delete_oldest_message,
    log_router_analytics
)
from app.services.converter import fetch_and_convert_url
import re

router = APIRouter()
logger = logging.getLogger(__name__)

# Supported MIME types for file upload
# Grouped by category for clarity
SUPPORTED_MIME_TYPES = {
    # ── Legal documents (text-based, converted via MarkItDown if needed) ─────
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",                                                                # → converted to MD
    "application/msword",                                                       # .doc → converted to MD
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx → converted to MD
    "application/rtf",                                                          # .rtf → converted to MD
    "text/rtf",                                                                 # .rtf (alt MIME)
    # ── Images (evidence, scans, screenshots) ───────────────────────────────
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}

# Human-readable labels for error messages
_MIME_LABELS = {
    "application/pdf": "PDF",
    "text/plain": "plain text (.txt)",
    "text/markdown": "Markdown (.md)",
    "text/csv": "CSV",
    "text/html": "HTML",
    "application/msword": "Word document (.doc)",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word document (.docx)",
    "application/rtf": "RTF",
    "text/rtf": "RTF",
    "image/png": "PNG image",
    "image/jpeg": "JPEG image",
    "image/webp": "WebP image",
    "image/gif": "GIF image",
}

# Helpful suggestions for common rejected types
_REJECTION_HINTS = {
    "video/": "Videos are not supported. Please describe what you need help with instead.",
    "audio/": "Audio files are not supported.",
    "application/vnd.ms-excel": "Excel files are not supported. Please export your spreadsheet as CSV.",
    "application/vnd.openxmlformats-officedocument.spreadsheetml": "Excel files are not supported. Please export as CSV.",
    "application/vnd.ms-powerpoint": "PowerPoint files are not supported. Please export as PDF.",
    "application/vnd.openxmlformats-officedocument.presentationml": "PowerPoint files are not supported. Please export as PDF.",
    "application/zip": "ZIP archives are not supported. Please upload individual files.",
    "application/x-rar": "RAR archives are not supported. Please upload individual files.",
    "application/octet-stream": "Binary files are not supported. Please convert to a supported format (PDF, DOCX, TXT).",
}

def _get_rejection_hint(mime: str) -> str:
    """Returns a helpful error message for a rejected MIME type."""
    for prefix, hint in _REJECTION_HINTS.items():
        if mime.startswith(prefix) or mime == prefix:
            return hint
    return f"File type '{mime}' is not supported. Accepted types: PDF, DOCX, DOC, TXT, MD, CSV, RTF, HTML, and images (PNG, JPEG, WebP, GIF)."


class FileAttachment(BaseModel):
    uri: str = Field(..., description="Google GenAI File URI")
    mime_type: str = Field(..., description="MIME type of the file")
    name: str = Field(..., description="Internal Google name for the file")
    display_name: str = Field(..., description="Original filename")
    s3_key: Optional[str] = Field(default=None, description="S3/R2 storage key for persistent access")

class ChatRequest(BaseModel):
    question: str = Field(default="", description="The legal question to ask AdvoAI.")
    session_id: Optional[str] = Field(default=None, description="Chat session ID for conversation continuity.")
    top_k: int = Field(default=5, description="Number of chunks to retrieve.")
    attachments: Optional[List[FileAttachment]] = Field(default=None, description="Optional list of attached files.")

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

@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    user: Optional[dict] = Depends(get_current_user)
):
    """
    Uploads a file to Google GenAI for multimodal analysis.

    Pipeline:
      1. Validate MIME type against curated whitelist
      2. Enforce 10MB size limit
      3. For convertible types (HTML, DOC, DOCX, RTF, CSV): convert to clean Markdown via MarkItDown
      4. Upload final file (original or converted .md) to Gemini Files API
      5. Wait for ACTIVE state (required for documents)
      6. Return URI, MIME type, and display name to frontend
    """
    MAX_FILE_SIZE_MB = 10
    MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    original_tmp_path: Optional[str] = None
    converted_tmp_path: Optional[str] = None

    try:
        client = await get_llm_client()

        # ── 1. Validate MIME type ────────────────────────────
        content_type = file.content_type or ""
        base_mime = content_type.split(";")[0].strip().lower()

        if base_mime not in SUPPORTED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail=_get_rejection_hint(base_mime)
            )

        is_image = base_mime.startswith("image/")
        upload_type = "image" if is_image else "doc"
        await check_upload_limit(request, user, upload_type)

        # ── 2. Read and validate size ────────────────────────
        content = await file.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({len(content) // (1024*1024)}MB). Maximum allowed size is {MAX_FILE_SIZE_MB}MB."
            )

        display_name = file.filename or "uploaded_file"
        logger.info(f"Processing upload: '{display_name}' ({base_mime}, {len(content):,} bytes)")

        # ── 3. Save original to temp file ───────────────────
        def _save(data: bytes, suffix: str) -> str:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                return tmp.name

        ext = os.path.splitext(display_name)[1] or ".bin"
        original_tmp_path = await asyncio.to_thread(_save, content, ext)

        # ── 4. Convert if needed (MarkItDown) ───────────────
        upload_path = original_tmp_path
        upload_mime = base_mime
        upload_display_name = display_name

        if base_mime in CONVERTIBLE_MIME_TYPES:
            markdown_text = await convert_to_markdown(original_tmp_path, base_mime, display_name)
            if markdown_text:
                converted_tmp_path = await save_markdown_as_tempfile(markdown_text, display_name)
                upload_path = converted_tmp_path
                upload_mime = "text/markdown"
                upload_display_name = os.path.splitext(display_name)[0] + ".md"
                logger.info(f"Using converted Markdown file for upload: '{upload_display_name}'")

        # ── 5. Upload to Gemini Files API ────────────────────
        uploaded_file = await client.client.aio.files.upload(
            file=upload_path,
            config={
                "display_name": upload_display_name,
                "mime_type": upload_mime,
            }
        )

        # ── 6. Wait for ACTIVE state ─────────────────────────
        max_wait_seconds = 30
        waited = 0
        while getattr(uploaded_file, "state", None) and \
              str(uploaded_file.state).upper() not in ("ACTIVE", "FILE_STATE_ACTIVE", "2"):
            if waited >= max_wait_seconds:
                logger.warning(f"File {uploaded_file.name} not ACTIVE after {max_wait_seconds}s, proceeding.")
                break
            await asyncio.sleep(2)
            waited += 2
            uploaded_file = await client.client.aio.files.get(name=uploaded_file.name)
            logger.debug(f"File state: {uploaded_file.state} (waited {waited}s)")

        logger.info(f"File ready: {uploaded_file.name} | state={getattr(uploaded_file, 'state', 'unknown')}")

        # ── 7. Upload to S3/R2 (Parallel or Sequential) ───────
        s3_key = await upload_file_to_s3(upload_path, upload_display_name, upload_mime)

        return {
            "uri": uploaded_file.uri,
            "mime_type": uploaded_file.mime_type or upload_mime,
            "name": uploaded_file.name,
            "display_name": display_name,  # Always show the original filename to the user
            "s3_key": s3_key,
        }

    except HTTPException:
        raise
    except ValueError as ve:
        # MarkItDown conversion errors — user-friendly message
        logger.warning(f"Conversion error for '{file.filename}': {ve}")
        raise HTTPException(status_code=422, detail=str(ve))
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
    finally:
        # Always clean up temp files
        for path in [original_tmp_path, converted_tmp_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

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
        # If user attached files, let the router know in the question text
        router_question = request.question
        if request.attachments:
            router_question += f" [User attached {len(request.attachments)} file(s)]"

        routing_data = await client.route_query(router_question, recent_messages=recent_messages if session_id else None)
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

        # ── 2.5 Extract & Fetch URLs from Prompt ────────────
        url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
        urls = list(set(url_pattern.findall(request.question)))
        url_contexts = []
        url_parents = []
        if urls:
            logger.info(f"Detected {len(urls)} URLs in prompt. Fetching...")
            fetch_tasks = [fetch_and_convert_url(url) for url in urls]
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for url, res in zip(urls, results):
                if isinstance(res, str):
                    url_contexts.append(f"### Content from {url}\n{res}")
                    url_parents.append({
                        "source_doc_id": f"url-{url}",
                        "root_title": f"Webpage: {url}",
                        "source_url": url,
                        "full_markdown": res
                    })
                else:
                    logger.warning(f"Failed to fetch {url}: {res}")

        # ── 3. RAG retrieval pipeline (if needed) ───────────
        rag_result = {}
        if intent == "create_contract":
            from app.services.templates import get_template
            contract_type = routing_data.get("contract_type", "")
            template_text = get_template(contract_type)
            rag_result = {
                "context_markdown": f"## RECOMMENDED TEMPLATE FOR DRAFTING\n{template_text}\n\nNote to AI: Use this template as a starting point. Adjust it based on the user's specific details or requests.",
                "parent_documents": []
            }
        elif intent == "compare_contracts":
            # For comparison, we rely entirely on the attachments provided by the user.
            rag_result = {
                "context_markdown": "Note to AI: The user wants to compare the attached documents. Focus entirely on analyzing the attachments.",
                "parent_documents": []
            }
        elif not is_conversational:
            # If user has attached files, skip RAG injection entirely.
            # The attachment IS the context — combining both risks token overflow.
            if request.attachments:
                logger.info("Attachments present — skipping RAG retrieval to avoid token overflow.")
                rag_result = {
                    "context_markdown": "",
                    "parent_documents": [],
                }
            else:
                rag_result = await retrieve_context(
                    question=search_query,
                    top_k=ideal_top_k,
                )

                # Guard: no documents found and no URL context
                if not rag_result.get("parent_documents") and not url_contexts:
                    return {
                        "answer": "I couldn't find any relevant legal documents in my database to answer this question. Please try rephrasing or asking about a different topic.",
                        "sources": [],
                        "citations": [],
                        "session_id": session_id,
                        "chunks_used": 0,
                        "intent": intent,
                    }

        # Inject URL context into the rag result
        if url_contexts:
            combined_url_text = "\n\n".join(url_contexts)
            existing_context = rag_result.get("context_markdown", "")
            rag_result["context_markdown"] = existing_context + "\n\n" + combined_url_text if existing_context else combined_url_text
            
            # Add them as sources with proper metadata for citations
            parents = rag_result.get("parent_documents", [])
            parents.extend(url_parents)
            rag_result["parent_documents"] = parents

        # ── 4. Verify & Re-upload Expired Attachments ───────
        if request.attachments:
            for att in request.attachments:
                try:
                    await client.client.aio.files.get(name=att.name)
                except Exception as e:
                    logger.warning(f"Gemini file {att.name} expired or not found. Attempting S3 re-upload. Error: {e}")
                    if att.s3_key:
                        tmp_path = await download_file_from_s3(att.s3_key)
                        if tmp_path:
                            # Re-upload to Gemini
                            try:
                                uploaded_file = await client.client.aio.files.upload(
                                    file=tmp_path,
                                    config={
                                        "display_name": att.display_name,
                                        "mime_type": att.mime_type,
                                    }
                                )
                                # Wait for ACTIVE state
                                max_wait_seconds = 30
                                waited = 0
                                while getattr(uploaded_file, "state", None) and \
                                      str(uploaded_file.state).upper() not in ("ACTIVE", "FILE_STATE_ACTIVE", "2"):
                                    if waited >= max_wait_seconds:
                                        break
                                    await asyncio.sleep(2)
                                    waited += 2
                                    uploaded_file = await client.client.aio.files.get(name=uploaded_file.name)
                                
                                # Update attachment references
                                att.uri = uploaded_file.uri
                                att.name = uploaded_file.name
                                logger.info(f"Successfully re-uploaded {att.s3_key} to Gemini: {att.name}")
                            except Exception as up_err:
                                logger.error(f"Failed to re-upload to Gemini: {up_err}")
                            finally:
                                import os
                                try:
                                    os.remove(tmp_path)
                                except Exception:
                                    pass

        # ── 5. Generate answer with Main LLM ────────────────
        try:
            llm_result = await client.ask(
                question=request.question,
                structured_history=recent_messages,
                context_markdown=rag_result.get("context_markdown", ""),
                session_summary=session_summary,
                is_conversational=is_conversational,
                attachments=request.attachments
            )
        except (OSError, ConnectionError) as net_err:
            logger.error(f"LLM network error (DNS/connection): {net_err}")
            raise HTTPException(
                status_code=503,
                detail="Unable to reach the AI service. Please check your internet connection and try again."
            )
        except Exception as llm_err:
            err_str = str(llm_err)
            if "token count exceeds" in err_str or "INVALID_ARGUMENT" in err_str:
                raise HTTPException(
                    status_code=413,
                    detail="The attached file is too large for the AI to process in a single request. Please try a smaller file or ask about a specific section of the document."
                )
            raise

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
