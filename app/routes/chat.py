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
from app.services.llm_client import get_llm_client, EmptyLLMResponse
from app.services.converter import (
    CONVERTIBLE_MIME_TYPES,
    convert_to_markdown,
    save_markdown_as_tempfile,
)
from app.services.rate_limiter import check_upload_limit
from app.services.storage import upload_file_to_s3, download_file_from_s3, generate_presigned_url
from app.middleware import require_rate_limit, get_current_user
from app.database.queries import (
    get_session_by_id, update_session_summary,
    create_session, rename_session,
    insert_message, get_session_messages, delete_oldest_message,
    log_router_analytics, create_uploaded_document, get_uploaded_documents,
    documents_not_owned_by, delete_message
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


_EXTENSION_MIMES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf": "application/rtf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _resolve_mime(content_type: str, filename: str) -> str:
    """Falls back to the file extension when the browser reports nothing useful.

    Browsers frequently send an empty type or application/octet-stream for .docx on
    Linux and Android. The client accepts those on extension, so the server rejecting
    them on content_type alone produced a 415 whose message listed DOCX as supported.
    """
    base = (content_type or "").split(";")[0].strip().lower()
    if base and base != "application/octet-stream" and base in SUPPORTED_MIME_TYPES:
        return base
    ext = os.path.splitext(filename or "")[1].lower()
    return _EXTENSION_MIMES.get(ext, base)


async def _rollback_user_message(message_id: Optional[str]) -> None:
    """Removes a pre-saved user turn after the request failed.

    The turn is written before generation so a mid-flight reload still shows it, but
    leaving it behind on failure meant every retry appended another copy of the same
    question to the transcript.
    """
    if not message_id:
        return
    try:
        await delete_message(message_id)
    except Exception:
        logger.warning(f"Could not roll back pre-saved user message {message_id}")


def safe_citations_for_storage(rag_result: dict, is_conversational: bool) -> list:
    """Citation metadata for the `sources` column.

    Deliberately omits `text`: a parent document is 20k+ chars and there can be five
    of them per answer, so persisting the bodies would bloat every row. Titles and
    links are enough to re-render the citation chips after a reload.
    """
    if is_conversational:
        return []
    seen, out = set(), []
    for doc in rag_result.get("parent_documents", []):
        doc_id = doc.get("source_doc_id")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append({
            "id": doc_id,
            "title": doc.get("root_title", "Untitled Document"),
            "source_url": doc.get("source_url", "#"),
        })
    return out


def _read_text_file(path: str) -> str:
    """Reads an already-textual upload off disk, tolerating imperfect encodings."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _gemini_expiry(uploaded_file: Any):
    """Real expiry from the Files API response, falling back to the documented 48h."""
    from datetime import datetime, timedelta, timezone
    raw = getattr(uploaded_file, "expiration_time", None)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) + timedelta(hours=48)


def _file_state_name(file: Any) -> str:
    """Normalises a Gemini FileState to its bare name, e.g. 'ACTIVE'.

    google-genai returns an enum whose str() is 'FileState.ACTIVE', so the old
    `str(state).upper() in ("ACTIVE", ...)` check was ALWAYS false — every upload
    burned the full 30s timeout and then proceeded with an unverified file.
    """
    state = getattr(file, "state", None)
    if state is None:
        return "ACTIVE"  # older responses omit state entirely; treat as ready
    return str(getattr(state, "name", state)).upper()


async def _await_file_active(client: Any, file: Any, max_wait_seconds: int = 30) -> Any:
    """Polls a freshly uploaded Gemini file until it leaves PROCESSING.

    Raises HTTPException if the file fails to process — previously the loop just
    logged a warning and handed a non-ACTIVE file to generate_content.
    """
    waited = 0
    while _file_state_name(file) == "PROCESSING":
        if waited >= max_wait_seconds:
            raise HTTPException(
                status_code=504,
                detail="The file is taking too long to process. Please try again, "
                       "or upload a smaller file.",
            )
        await asyncio.sleep(1)
        waited += 1
        file = await client.client.aio.files.get(name=file.name)
        logger.debug(f"File state: {file.state} (waited {waited}s)")

    state = _file_state_name(file)
    if state == "FAILED":
        raise HTTPException(
            status_code=422,
            detail="The AI service could not process this file. It may be corrupted "
                   "or in an unsupported sub-format.",
        )

    logger.info(f"File ready in {waited}s: {file.name} | state={state}")
    return file


# How many recent messages are replayed to the LLM each turn. Deliberately smaller than
# the DB retention window (MAX_WINDOW_MESSAGES) — retention protects history from being
# deleted, this bounds per-turn token cost.
LLM_HISTORY_MESSAGES = 12


class FileAttachment(BaseModel):
    """An attachment on the current turn.

    `document_id` is the modern form and the only one that survives across turns.

    The Gemini-handle fields are legacy. They are still ACCEPTED, but note the limit
    of that compatibility: text documents (docx/doc/rtf/csv/html) now return
    `uri: null`, and an old client bundle filters attachments on `f.uri`, so such a
    client will silently drop a .docx it just uploaded. Images and PDFs still carry a
    uri and keep working. This self-heals on refresh; it is a rollout window, not a
    supported mode.
    """
    document_id: Optional[str] = Field(default=None, description="uploaded_documents.id")
    uri: Optional[str] = Field(default=None, description="Legacy Google GenAI File URI")
    mime_type: Optional[str] = Field(default=None, description="MIME type of the file")
    name: Optional[str] = Field(default=None, description="Legacy internal Google file name")
    display_name: Optional[str] = Field(default=None, description="Original filename")
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
        # Retention: how many messages stay in the DB before being folded into the
        # archive summary. Commit 97db6a3 raised this to 40 to stop premature deletion;
        # 7093947 silently regressed it to 10. Note this is NOT the same knob as how
        # many messages get fed to the LLM (see LLM_HISTORY_MESSAGES).
        MAX_WINDOW_MESSAGES = 40
        current_messages = await get_session_messages(session_id, limit=100)
        
        if len(current_messages) > MAX_WINDOW_MESSAGES:
            dropped_messages = []
            while len(current_messages) > MAX_WINDOW_MESSAGES:
                oldest = await delete_oldest_message(session_id)
                if oldest:
                    dropped_messages.append(oldest)
                    current_messages.pop(0)
            
            if dropped_messages:
                def _render(msg):
                    line = f"{msg['role'].capitalize()}: {msg['content']}"
                    # Record that a document was involved, otherwise folding a message
                    # into the summary erases every trace that it ever existed.
                    names = [a.get("display_name") for a in (msg.get("attachments") or [])
                             if isinstance(a, dict) and a.get("display_name")]
                    if names:
                        line += f"\n[attached: {', '.join(names)}]"
                    return line

                dropped_text = "\n\n".join(_render(msg) for msg in dropped_messages)
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
        base_mime = _resolve_mime(file.content_type or "", file.filename or "")

        if base_mime not in SUPPORTED_MIME_TYPES:
            logger.info(
                f"Rejected upload '{file.filename}': browser sent "
                f"content_type={file.content_type!r}, resolved to {base_mime!r}"
            )
            raise HTTPException(
                status_code=415,
                detail=_get_rejection_hint(base_mime)
            )

        is_image = base_mime.startswith("image/")
        upload_type = "image" if is_image else "doc"
        await check_upload_limit(request, user, upload_type)

        # ── 2. Stream to temp file and validate size ────────
        display_name = file.filename or "uploaded_file"
        ext = os.path.splitext(display_name)[1] or ".bin"

        CHUNK = 1024 * 1024  # 1 MB

        def _open_tmp(suffix: str) -> str:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.close()
            return tmp.name

        original_tmp_path = await asyncio.to_thread(_open_tmp, ext)

        total = 0
        with open(original_tmp_path, "wb") as fout:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum allowed size is {MAX_FILE_SIZE_MB}MB."
                    )
                fout.write(chunk)

        logger.info(f"Processing upload: '{display_name}' ({base_mime}, {total:,} bytes)")

        # ── 4. Convert if needed (MarkItDown) ───────────────
        # Text-ish documents become Markdown and are stored as TEXT. They are never sent
        # to the Gemini Files API, which is what used to make them expire after 48h or
        # become unreadable (403) once ApiKeyManager rotated to a different key.
        upload_path = original_tmp_path
        upload_mime = base_mime
        upload_display_name = display_name
        markdown_text: Optional[str] = None

        if base_mime in CONVERTIBLE_MIME_TYPES:
            markdown_text = await convert_to_markdown(original_tmp_path, base_mime, display_name)
            if markdown_text:
                converted_tmp_path = await save_markdown_as_tempfile(markdown_text, display_name)
                upload_path = converted_tmp_path
                upload_mime = "text/markdown"
                upload_display_name = os.path.splitext(display_name)[0] + ".md"
                logger.info(f"Converted '{display_name}' to Markdown ({len(markdown_text):,} chars)")
        elif base_mime in ("text/plain", "text/markdown"):
            # Already text — read it straight off disk, no conversion needed.
            markdown_text = await asyncio.to_thread(_read_text_file, original_tmp_path)

        is_text_doc = markdown_text is not None and markdown_text.strip() != ""
        kind = "text" if is_text_doc else "media"

        # ── 5. Persist to S3/R2 (used for previews and for media re-minting) ──
        s3_key = await upload_file_to_s3(upload_path, upload_display_name, upload_mime)
        if not s3_key and not is_text_doc:
            logger.warning(
                f"S3/R2 is not configured — media file '{display_name}' has no durable copy "
                f"and will become unreadable once its Gemini handle expires."
            )

        # ── 6. Media only: upload to Gemini Files API ────────
        gemini_uri = gemini_name = gemini_mime = None
        gemini_expires_at = None
        if not is_text_doc:
            uploaded_file = await client.client.aio.files.upload(
                file=upload_path,
                config={
                    "display_name": upload_display_name,
                    "mime_type": upload_mime,
                }
            )
            uploaded_file = await _await_file_active(client, uploaded_file)
            gemini_uri = uploaded_file.uri
            gemini_name = uploaded_file.name
            gemini_mime = uploaded_file.mime_type or upload_mime
            gemini_expires_at = _gemini_expiry(uploaded_file)

        # ── 7. Record in the durable document store ──────────
        document_id = await create_uploaded_document(
            user_id=user["id"] if user else None,
            display_name=display_name,   # always the ORIGINAL filename
            original_mime=upload_mime,
            kind=kind,
            extracted_text=markdown_text if is_text_doc else None,
            s3_key=s3_key,
            gemini_uri=gemini_uri,
            gemini_name=gemini_name,
            gemini_mime=gemini_mime,
            gemini_expires_at=gemini_expires_at,
        )

        return {
            "document_id": document_id,
            "kind": kind,
            "display_name": display_name,  # Always show the original filename to the user
            "mime_type": base_mime,        # the ORIGINAL type, so the UI shows "DOCX"
            "s3_key": s3_key,
            # Legacy fields. Populated only for media — text documents no longer touch
            # the Files API, so a stale client that filters on `uri` will drop them.
            # See FileAttachment for the rollout caveat.
            "uri": gemini_uri,
            "name": gemini_name,
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
            recent_messages = await get_session_messages(session_id, limit=LLM_HISTORY_MESSAGES)

        # ── 1b. Resolve this turn's attachments (needed BEFORE routing) ──
        document_ids = [att.document_id for att in (request.attachments or []) if att.document_id]
        legacy_attachments = [att for att in (request.attachments or [])
                              if not att.document_id and att.uri]

        # document_ids arrive straight from the client, so ownership must be checked
        # here. (History rehydration needs no check — session ownership already gates it.)
        if document_ids:
            forbidden = await documents_not_owned_by(document_ids, user["id"] if user else None)
            if forbidden:
                raise HTTPException(
                    status_code=403,
                    detail="One or more attached files are not available to you.",
                )

        # ── 2. Intent Routing ───────────────────────────────
        # The router used to receive only "[User attached N file(s)]" — a count, never
        # content. It therefore could not tell an employment contract from a selfie, and
        # bucketed attachment turns into an intent that skipped retrieval entirely. Give
        # it the actual text so it can classify and build a search query from the subject
        # matter of the document.
        document_excerpts = []
        if document_ids:
            try:
                for doc in await get_uploaded_documents(document_ids):
                    if doc.get("extracted_text"):
                        document_excerpts.append({
                            "display_name": doc["display_name"],
                            "text": doc["extracted_text"],
                        })
            except Exception:
                logger.exception("Could not load document excerpts for routing; routing on text alone")

        router_question = request.question
        if request.attachments and not document_excerpts:
            # Images and PDFs have no extracted text — fall back to the old hint so the
            # router at least knows something is attached.
            router_question += f" [User attached {len(request.attachments)} file(s)]"

        import time
        start_router = time.monotonic()
        routing_data = await client.route_query(
            router_question,
            recent_messages=recent_messages if session_id else None,
            document_excerpts=document_excerpts,
        )
        router_time_ms = int((time.monotonic() - start_router) * 1000)
        
        intent = routing_data.get("intent", "legal_rag")
        search_query = routing_data.get("search_query", request.question)
        ideal_top_k = routing_data.get("ideal_top_k", request.top_k)
        
        # Override Top-K if enabled in settings
        from app.database.queries import get_setting
        override_enabled = await get_setting("override_rag_top_k") == "true"
        if override_enabled:
            global_top_k = await get_setting("rag_top_k")
            if global_top_k and global_top_k.isdigit():
                ideal_top_k = int(global_top_k)
                logger.info(f"Overriding Top-K with global setting: {ideal_top_k}")
        
        is_conversational = (intent == "conversational")
        logger.info(f"Query routed as: {intent} | Optimized Search Query: {search_query} | Ideal Top-K: {ideal_top_k} | Router Time: {router_time_ms}ms | Router Model: {client.router_model}")


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
        # The router used to be able to return `create_contract` / `compare_contracts`,
        # and both branches SKIPPED retrieval. In production that made them a dumping
        # ground for any turn with an attachment: "Bu shartnomada qonunga zid shartlar
        # bormi?" ("does this contract contain unlawful clauses?") was classified
        # compare_contracts and answered with ZERO statutes in context — 17% of all turns
        # received no legal grounding this way. Compare and Draft are now explicit
        # endpoints in documents.py, so the router no longer needs to guess them and
        # both intents have been removed from ROUTER_SYSTEM_PROMPT.
        #
        # A stale model or a cached prompt override could still emit them, so treat any
        # non-conversational intent as a retrieval intent rather than trusting the label.
        if intent in ("create_contract", "compare_contracts"):
            logger.warning(
                f"Router returned legacy intent '{intent}'; treating as legal_rag. "
                f"Check custom_router_prompt if this persists."
            )
            intent = "legal_rag"
            is_conversational = False

        if not is_conversational:
            # We always run RAG if it's not conversational, even with attachments!
            # Gemini 3 has a massive context window, so token overflow is not a concern.
            rag_result = await retrieve_context(
                question=search_query,
                top_k=ideal_top_k,
            )

            # Guard: no documents found and no URL context
            if not rag_result.get("parent_documents") and not url_contexts:
                # If there are attachments, we still want the LLM to process them!
                if not request.attachments:
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

        # ── 4. Verify legacy inline attachments ─────────────
        # document_ids were resolved and ownership-checked in §1b (the router needs them).
        # Documents from the durable store need no liveness check: text documents are read
        # straight from Postgres, and media handles are re-minted on demand inside
        # build_parts(). Only legacy inline Gemini URIs still need one.
        for att in legacy_attachments:
            if not att.name:
                continue
            try:
                await client.client.aio.files.get(name=att.name)
            except Exception as e:
                logger.warning(f"Legacy Gemini file {att.name} is not accessible: {e}")
                raise HTTPException(
                    status_code=410,
                    detail=f"'{att.display_name or 'The attached file'}' is no longer available. "
                           f"Please attach it again.",
                )

        # ── 5a. Pre-save user message so reloads show it mid-processing ──
        # Persist EVERY attachment, keyed by document_id. The old code filtered on
        # `if att.s3_key`, so when R2 was unconfigured the attachment vanished from
        # history entirely — the chat showed a file the model could never see again.
        attachment_meta = None
        user_message_id: Optional[str] = None
        if session_id:
            try:
                if request.attachments:
                    attachment_meta = [
                        {
                            "document_id": att.document_id,
                            "s3_key": att.s3_key or None,
                            "display_name": att.display_name,
                            "mime_type": att.mime_type,
                        }
                        for att in request.attachments
                    ] or None
                user_message_id = await insert_message(
                    session_id, "user", request.question, attachments=attachment_meta)
            except Exception as se:
                logger.warning(f"Pre-save user message failed (non-fatal): {se}")

        # ── 5b. Generate answer with Main LLM ───────────────
        # The user turn was pre-saved above so a mid-flight reload still shows it.
        # If generation fails we must remove it again, or each retry appends another
        # copy of the same question to the transcript.
        llm_time_ms = None
        try:
            start_llm = time.monotonic()
            llm_result = await client.ask(
                question=request.question,
                structured_history=recent_messages,
                context_markdown=rag_result.get("context_markdown", ""),
                session_summary=session_summary,
                is_conversational=is_conversational,
                attachments=legacy_attachments,
                document_ids=document_ids,
            )
            llm_time_ms = int((time.monotonic() - start_llm) * 1000)
            logger.info(f"✅ Main LLM ({client.main_model}) generation completed in {llm_time_ms}ms")
        except (OSError, ConnectionError) as net_err:
            logger.error(f"LLM network error (DNS/connection): {net_err}")
            raise HTTPException(
                status_code=503,
                detail="Unable to reach the AI service. Please check your internet connection and try again."
            )
        except EmptyLLMResponse as empty_err:
            logger.error(f"LLM returned no text (finish_reason={empty_err.finish_reason})")
            if empty_err.finish_reason == "MAX_TOKENS":
                detail = ("The response was too long to complete. Please ask about a "
                          "narrower part of the document.")
            elif empty_err.finish_reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
                detail = ("The AI declined to answer this request. Please rephrase your "
                          "question.")
            else:
                detail = ("The AI returned an empty response. Please try again or "
                          "rephrase your question.")
            raise HTTPException(status_code=502, detail=detail)
        except Exception as llm_err:
            err_str = str(llm_err)
            # Only a genuine context-window overflow is a "file too large" problem.
            # The old check also swallowed every INVALID_ARGUMENT — and, by falling
            # through, every 403 PERMISSION_DENIED from a stale file URI became an
            # opaque 500. Log the real error and report something actionable.
            logger.exception("Main LLM generation failed")
            if "token count exceeds" in err_str or "exceeds the maximum number of tokens" in err_str:
                raise HTTPException(
                    status_code=413,
                    detail="The attached file is too large for the AI to process in a single request. Please try a smaller file or ask about a specific section of the document."
                )
            if "PERMISSION_DENIED" in err_str or "NOT_FOUND" in err_str:
                raise HTTPException(
                    status_code=410,
                    detail="One of the attached files is no longer available to the AI. "
                           "Please attach it again."
                )
            raise

        # ── 5c. Save assistant reply & run history maintenance ─
        if session_id:
            try:
                # Persist citations too — the `sources` column existed but was never
                # written, so a cross-device reload silently lost every citation.
                await insert_message(
                    session_id, "assistant", llm_result["answer"],
                    sources=safe_citations_for_storage(rag_result, is_conversational),
                )

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
                
        # Log analytics in the background
        background_tasks.add_task(
            log_router_analytics,
            session_id,
            request.question,
            intent,
            search_query,
            ideal_top_k,
            router_time_ms,
            llm_time_ms
        )

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
        await _rollback_user_message(locals().get("user_message_id"))
        raise
    except ValueError as ve:
        await _rollback_user_message(locals().get("user_message_id"))
        logger.exception("Configuration error")
        raise HTTPException(status_code=500, detail="Server configuration error. Ensure API keys are set.")
    except Exception:
        await _rollback_user_message(locals().get("user_message_id"))
        logger.exception("Chat API error")
        raise HTTPException(status_code=500, detail="An error occurred while processing your request.")


@router.get("/file/{s3_key:path}")
async def get_attachment_url(s3_key: str, request: Request):
    """
    Returns a short-lived presigned URL for a file stored in R2.
    Requires authentication to prevent unauthorized access.
    The URL itself is time-limited (1 hour), so it's safe to expose to clients.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    url = await generate_presigned_url(s3_key, expires_in=3600)
    if not url:
        raise HTTPException(
            status_code=404,
            detail="File not found or storage is not configured."
        )

    return {"url": url}
