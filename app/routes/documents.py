"""documents.py — Compare and Draft as real features.

These replace the old pattern where the UI prefilled the chat box with a canned prompt
and the backend had an LLM guess the user's intent back out of that prose. Here the UI
states what it wants, so invocation is deterministic and the router is left to do what
it is good at: classifying freeform chat.

Both endpoints write into a chat session, so both must verify session ownership before
inserting — the same check `chat.py` performs.
"""

import logging
import os
import shutil
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Depends, Request, Response
from pydantic import BaseModel, Field

from app.database.queries import (
    get_session_by_id, create_session, insert_message,
    get_uploaded_documents, documents_not_owned_by, create_uploaded_document,
    resolve_legal_document, fetch_document_parts_page, fetch_document_parts_meta,
)
from app.middleware import require_rate_limit
from app.services.attachments import build_parts
from app.services.docgen import render_contract, render_contract_pdf, contract_to_markdown, DOCX_MIME, PDF_MIME
from app.services.llm_client import get_llm_client, EmptyLLMResponse
from app.services.rag_pipeline import retrieve_context
from app.services.storage import upload_file_to_s3
from app.services.templates import get_template

router = APIRouter()
logger = logging.getLogger(__name__)

MIN_COMPARE_DOCS = 2
MAX_COMPARE_DOCS = 4

# A full legal code can run 100-250 parts at ~20k chars each — paginate rather than
# materialize the whole thing on every panel open.
DEFAULT_FULL_DOC_PART_LIMIT = 20
MAX_FULL_DOC_PART_LIMIT = 50


class CompareRequest(BaseModel):
    document_ids: List[str] = Field(..., description="2-4 uploaded document ids")
    session_id: Optional[str] = Field(default=None)
    language: Optional[str] = Field(default=None, description="Language hint for the response")


class DraftRequest(BaseModel):
    contract_type: str = Field(..., description="e.g. NDA, Employment, Lease, Service")
    answers: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = Field(default=None)
    language: str = Field(default="uz")
    format: Literal["docx", "pdf"] = Field(default="docx")


def _require_user(user: Optional[dict]) -> dict:
    """`require_rate_limit` yields None for guests, but both endpoints here write into
    a chat session and `chat_sessions.user_id` is NOT NULL. Without this guard the
    `user["id"]` lookups below raise TypeError and surface as an opaque 500 — exactly
    the error shape this work set out to remove.
    """
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Please sign in to use this feature.",
        )
    return user


async def _resolve_session(session_id: Optional[str], user: dict) -> Optional[str]:
    """Returns a session id the user owns, creating one when none was supplied."""
    if session_id:
        session = await get_session_by_id(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found.")
        if session["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Access denied to this session.")
        return session["id"]
    session = await create_session(user["id"])
    return session["id"]


async def _assert_documents_readable(document_ids: List[str], user: dict) -> None:
    forbidden = await documents_not_owned_by(document_ids, user["id"])
    if forbidden:
        raise HTTPException(
            status_code=403,
            detail="One or more of those documents are not available to you.",
        )


@router.post("/compare")
async def compare_documents(request: CompareRequest, user: Optional[dict] = Depends(require_rate_limit)):
    """Clause-by-clause comparison of 2-4 documents, returned as structured JSON."""
    user = _require_user(user)
    doc_ids = list(dict.fromkeys(request.document_ids or []))  # de-dupe, keep order

    if not (MIN_COMPARE_DOCS <= len(doc_ids) <= MAX_COMPARE_DOCS):
        raise HTTPException(
            status_code=422,
            detail=f"Please provide between {MIN_COMPARE_DOCS} and {MAX_COMPARE_DOCS} documents to compare.",
        )

    await _assert_documents_readable(doc_ids, user)

    docs = await get_uploaded_documents(doc_ids)
    if len(docs) != len(doc_ids):
        raise HTTPException(status_code=404, detail="One or more documents could not be found.")

    client = await get_llm_client()

    # Both kinds are welcome: text documents inline, PDFs and scans go through the
    # Files API. Contracts arrive as PDFs constantly, so restricting to text would
    # reject the most common real input.
    parts = await build_parts(client, doc_ids)
    if not parts:
        raise HTTPException(status_code=422, detail="The documents could not be read for comparison.")

    labels = [d["display_name"] for d in docs]

    try:
        result = await client.compare_documents(parts, labels, request.language or "")
    except EmptyLLMResponse as e:
        logger.error(f"Comparison produced no usable output (finish_reason={e.finish_reason})")
        raise HTTPException(
            status_code=502,
            detail="The comparison could not be completed. Please try again.",
        )

    # Attach the real filenames so the UI never has to guess which column is which.
    result["documents"] = [
        {"id": chr(65 + i), "label": labels[i], "document_id": doc_ids[i]}
        for i in range(len(doc_ids))
    ]

    session_id = await _resolve_session(request.session_id, user)

    # Persist as a normal turn so the session stays continuous and "Continue in chat"
    # lands in a conversation that already knows what was compared.
    await insert_message(
        session_id, "user",
        f"Taqqoslash: {', '.join(labels)}",
        attachments=[
            {
                "document_id": d["id"],
                "display_name": d["display_name"],
                "mime_type": d["original_mime"],
                "s3_key": d.get("s3_key"),
            }
            for d in docs
        ],
    )
    await insert_message(
        session_id, "assistant", result.get("summary", ""),
        sources={"kind": "comparison", "comparison": result},
    )

    return {"session_id": session_id, "comparison": result}


@router.post("/draft")
async def draft_contract(request: DraftRequest, user: Optional[dict] = Depends(require_rate_limit)):
    """Generates a contract and returns a real .docx the user can download."""
    user = _require_user(user)
    template_text = get_template(request.contract_type)

    # Ground the draft in the actual legislation where we can. Best-effort: a RAG
    # miss should degrade to a template-only draft, not fail the request.
    legal_context = ""
    try:
        rag = await retrieve_context(
            question=f"{request.contract_type} shartnoma majburiy shartlari talablari",
            top_k=3,
        )
        legal_context = rag.get("context_markdown", "") or ""
    except Exception:
        logger.exception("RAG lookup failed while drafting; continuing without legal context")

    client = await get_llm_client()
    try:
        contract = await client.draft_contract(
            template_text=template_text,
            contract_type=request.contract_type,
            answers=request.answers,
            legal_context=legal_context,
            language=request.language,
        )
    except EmptyLLMResponse as e:
        logger.error(f"Draft produced no usable output (finish_reason={e.finish_reason})")
        raise HTTPException(
            status_code=502,
            detail="The document could not be generated. Please try again.",
        )

    tmp_dir = None
    try:
        if request.format == "pdf":
            try:
                path = render_contract_pdf(contract)
            except Exception:
                logger.exception("PDF rendering failed")
                raise HTTPException(
                    status_code=502,
                    detail="The PDF could not be generated. Please try again, or choose Word format.",
                )
            mime_type = PDF_MIME
        else:
            path = render_contract(contract)
            mime_type = DOCX_MIME

        tmp_dir = os.path.dirname(path)
        file_name = os.path.basename(path)

        s3_key = await upload_file_to_s3(path, file_name, mime_type)
        if not s3_key:
            raise HTTPException(
                status_code=503,
                detail="Document storage is not configured, so the file cannot be delivered. "
                       "Please contact support.",
            )

        # Store the draft in the SAME durable store as an upload. Without this the
        # document's text lives nowhere the model can see, and the first follow-up
        # ("change the salary") would reproduce the amnesia bug on a brand-new feature.
        markdown = contract_to_markdown(contract)
        document_id = await create_uploaded_document(
            user_id=user["id"],
            display_name=file_name,
            original_mime=mime_type,
            kind="text",
            extracted_text=markdown,
            s3_key=s3_key,
        )

        session_id = await _resolve_session(request.session_id, user)
        chat_note = contract.get("chat_note") or "Here is your draft. Please review it carefully."

        await insert_message(
            session_id, "user",
            f"{request.contract_type} shartnomasini tayyorlang",
        )
        await insert_message(
            session_id, "assistant", chat_note,
            attachments=[{
                "document_id": document_id,
                "display_name": file_name,
                "mime_type": mime_type,
                "s3_key": s3_key,
            }],
        )

        return {
            "session_id": session_id,
            "document_id": document_id,
            "display_name": file_name,
            "mime_type": mime_type,
            "s3_key": s3_key,
            "chat_note": chat_note,
            "contract": contract,
        }
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/{key}/full/meta")
async def get_full_legal_document_meta(key: str, user: Optional[dict] = Depends(require_rate_limit)):
    """Part positions and sizes only — no `text` — for the InsightPanel minimap.

    Fetching this instead of the full document to draw the scroll rail is what keeps
    opening a citation from downloading a whole legal code just to place some dots.
    """
    _require_user(user)
    doc = await resolve_legal_document(key)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found.")

    parts = await fetch_document_parts_meta(doc["id"])
    return {**doc, "parts": parts, "total_parts": len(parts)}


@router.get("/{key}/full")
async def get_full_legal_document(
    key: str,
    request: Request,
    response: Response,
    offset: int = 0,
    limit: int = DEFAULT_FULL_DOC_PART_LIMIT,
    user: Optional[dict] = Depends(require_rate_limit),
):
    """A page of a legal document's parts, in order, for full-document viewing."""
    _require_user(user)
    offset = max(0, offset)
    limit = max(1, min(limit, MAX_FULL_DOC_PART_LIMIT))

    doc = await resolve_legal_document(key)
    if not doc:
        raise HTTPException(status_code=404, detail="Legal document not found.")

    parts, total = await fetch_document_parts_page(doc["id"], offset, limit)

    etag = f'W/"{doc["id"]}-{offset}-{limit}-{total}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=60"

    return {**doc, "parts": parts, "total_parts": total, "offset": offset, "limit": limit}
