"""
llm_client.py — Gemini & Gemma LLM Client

Handles RAG QA with Gemini 3.1 Pro/Flash, and Query Intent Routing 
& Summarization using Gemma 4 31B IT (with Thinking block parsing).
"""

import logging
import json
import re
import asyncio
import time
from typing import Dict, Any

from google import genai
from google.genai import types
from app.config import settings
from app.services.api_keys import api_key_manager
from app.services.prompts import (
    SYSTEM_PROMPT,
    RAG_USER_PROMPT_TEMPLATE,
    RAG_USER_PROMPT_NO_HISTORY_TEMPLATE,
    CONVERSATIONAL_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
    SUMMARY_FIRST_MESSAGE_TEMPLATE,
    TITLE_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    METADATA_EXTRACTION_SYSTEM_PROMPT,
    COMPARE_SYSTEM_PROMPT,
    DRAFT_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


class EmptyLLMResponse(RuntimeError):
    """The model returned no usable text (blocked, truncated, or no candidate at all).

    Carries the finish_reason so the route layer can tell the user something specific
    instead of a generic 500.
    """

    def __init__(self, finish_reason: str = "UNKNOWN"):
        self.finish_reason = finish_reason
        super().__init__(f"Model returned no text (finish_reason={finish_reason})")


def _finish_reason(response: Any) -> str:
    """Best-effort extraction of the finish reason from a GenerateContentResponse."""
    try:
        candidate = response.candidates[0]
        reason = candidate.finish_reason
        return str(getattr(reason, "name", reason) or "UNKNOWN")
    except Exception:
        return "UNKNOWN"


class GeminiClient:
    """
    Wraps Google's GenAI API for legal QA, routing, and summarization.
    Uses ApiKeyManager to handle rate limits with multiple keys.
    """

    def __init__(
        self, 
        main_model: str = "gemini-3.1-flash-lite", 
        router_model: str = "gemma-4-31b-it",
        custom_main_prompt: str = None,
        custom_router_prompt: str = None
    ):
        self.main_model = main_model
        self.router_model = router_model
        self.custom_main_prompt = custom_main_prompt
        self.custom_router_prompt = custom_router_prompt

        logger.info(f"🤖 Main LLM initialized: {self.main_model}")
        logger.info(f"🧠 Router LLM initialized: {self.router_model}")

    @property
    def client(self):
        """Returns the dynamically active genai.Client from the ApiKeyManager."""
        return api_key_manager.get_current_client()

    # 4xx statuses that will never succeed on retry. Burning the full backoff ladder on
    # these costs ~15s and 5 API calls for an outcome that is already decided — and it
    # was the reason a stale-file 403 looked like a slow generic server error.
    _NON_RETRYABLE = ("400", "401", "403", "404", "PERMISSION_DENIED",
                      "NOT_FOUND", "UNAUTHENTICATED", "INVALID_ARGUMENT")

    @staticmethod
    def _is_retryable(err_str: str) -> bool:
        """429/RESOURCE_EXHAUSTED are always worth retrying; other 4xx never are."""
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            return True
        return not any(marker in err_str for marker in GeminiClient._NON_RETRYABLE)

    async def _generate_with_retry(self, model: str, contents: Any, config: Any, max_retries: int = 5):
        """Helper to execute generate_content with exponential backoff retries and key rotation."""
        keys_tried = 1
        for attempt in range(max_retries):
            try:
                client = api_key_manager.get_current_client()
                return await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                err_str = str(e)
                if attempt == max_retries - 1:
                    logger.error(f"❌ LLM API failed after {max_retries} attempts: {e}")
                    raise

                if not self._is_retryable(err_str):
                    logger.error(f"❌ LLM API returned a non-retryable error, failing fast: {e}")
                    raise

                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if len(api_key_manager.keys) > 1 and keys_tried < len(api_key_manager.keys):
                        api_key_manager.rotate_key()
                        keys_tried += 1
                        logger.info(f"Retrying generation immediately with new API key...")
                        continue # retry immediately without sleeping

                wait_time = 2 ** attempt
                logger.warning(f"⚠️ LLM API error: {e}. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
                keys_tried = 1 # Reset after sleeping

    # How much of an attached document the router gets to see. Enough to identify the
    # document type and subject matter; small enough to keep routing fast and cheap.
    ROUTER_DOC_EXCERPT_CHARS = 2000

    async def route_query(
        self,
        question: str,
        recent_messages: list = None,
        document_excerpts: list = None,
    ) -> Dict[str, str]:
        """
        Uses gemma-4-31b-it to classify the query as 'conversational' or 'legal_rag'.

        `document_excerpts` is a list of {display_name, text} for files attached to THIS
        turn. Without it the router only knew a file count, so "is this legal?" plus an
        employment contract was indistinguishable from "is this legal?" plus a selfie —
        and such turns were being routed away from RAG, answering legal questions with
        no statutes in context.

        Returns a dict: {"intent": ..., "search_query": ..., "ideal_top_k": ...}
        """
        logger.info("🧠 Routing query intent...")
        
        config = types.GenerateContentConfig(
            system_instruction=self.custom_router_prompt or ROUTER_SYSTEM_PROMPT,
            temperature=0.0,  # Zero temperature for strict instruction following
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL")
        )
        
        sections = []
        if recent_messages:
            history_text = "\n".join(
                f"{msg['role'].capitalize()}: {msg['content']}"
                for msg in recent_messages
            )
            sections.append(f"Recent Conversation History:\n{history_text}")

        for doc in (document_excerpts or []):
            text = (doc.get("text") or "").strip()
            if not text:
                continue
            excerpt = text[: self.ROUTER_DOC_EXCERPT_CHARS]
            if len(text) > self.ROUTER_DOC_EXCERPT_CHARS:
                excerpt += "\n[...truncated...]"
            sections.append(
                f"Attached document excerpt ({doc.get('display_name', 'document')}):\n{excerpt}"
            )

        sections.append(f"Current Query: {question}")
        prompt = "\n\n".join(sections) if len(sections) > 1 else question

        response = await self._generate_with_retry(
            model=self.router_model,
            contents=prompt,
            config=config,
        )
        
        # Parse output safely, separating thoughts from the final response
        final_text = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if getattr(part, 'thought', False):
                    logger.debug(f"[Gemma Thought]: {part.text}")
                else:
                    final_text += part.text
        else:
            final_text = response.text
            
        final_text = final_text.strip()
        logger.debug(f"Router final text: {final_text}")
        
        # Extract JSON
        match = re.search(r"\{.*\}", final_text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                intent = data.get("intent", "legal_rag")
                search_query = data.get("search_query", "") if intent == "conversational" else data.get("search_query", question)

                # ROUTER_SYSTEM_PROMPT explicitly asks for these two. Dropping them meant
                # CONTRACT_TEMPLATES was unreachable and adaptive top-k never took effect.
                routed = {"intent": intent, "search_query": search_query}

                ideal_top_k = data.get("ideal_top_k")
                if isinstance(ideal_top_k, (int, float)) or (isinstance(ideal_top_k, str) and ideal_top_k.isdigit()):
                    routed["ideal_top_k"] = max(3, min(10, int(ideal_top_k)))

                contract_type = data.get("contract_type")
                if isinstance(contract_type, str) and contract_type.strip():
                    routed["contract_type"] = contract_type.strip()

                return routed
            except json.JSONDecodeError:
                pass

        # Fallback
        if "conversational" in final_text.lower():
            return {"intent": "conversational", "search_query": ""}
        return {"intent": "legal_rag", "search_query": question}

    async def ask(
        self,
        question: str,
        structured_history: list[Dict[str, Any]] = None,
        context_markdown: str = "",
        session_summary: str = "",
        is_conversational: bool = False,
        attachments: list = None,
        document_ids: list = None,
    ) -> Dict[str, Any]:
        """
        Answers the user's question. If is_conversational=True, ignores context_markdown.
        Uses structured_history for multi-turn conversational awareness natively.

        Attachments come in two forms:
          - `document_ids`: ids into `uploaded_documents` for the CURRENT turn.
          - `attachments`:  legacy inline {uri, mime_type} objects, kept so older
                            clients keep working during rollout.

        History attachments are rehydrated from each message's own `attachments`
        metadata. Replaying them is what stops the model from asking the user to
        re-upload a document that is sitting right there in the chat.
        """
        from app.services.attachments import build_parts, doc_ids_from_message

        payload = []

        # 1. Map prior history turns to Native Gemini Content objects.
        #    Each historical user turn is rebuilt WITH its documents, not just its text.
        if structured_history:
            for msg in structured_history:
                # 'user' or 'assistant' -> 'user' or 'model' for Gemini
                role = "user" if msg["role"] == "user" else "model"
                parts = []
                if role == "user":
                    hist_doc_ids = doc_ids_from_message(msg)
                    if hist_doc_ids:
                        try:
                            parts.extend(await build_parts(self, hist_doc_ids))
                        except Exception:
                            logger.exception("Failed to rehydrate history attachments; continuing with text only")
                parts.append(types.Part.from_text(text=msg["content"]))
                payload.append(types.Content(role=role, parts=parts))

        # 2. Build current turn (Context Injection)
        final_prompt = ""
        if is_conversational:
            if session_summary:
                final_prompt = CONVERSATIONAL_PROMPT_TEMPLATE.format(
                    session_summary=session_summary,
                    question=question,
                )
            else:
                final_prompt = question
        else:
            if session_summary:
                final_prompt = RAG_USER_PROMPT_TEMPLATE.format(
                    context_markdown=context_markdown,
                    session_summary=session_summary,
                    question=question,
                )
            else:
                final_prompt = RAG_USER_PROMPT_NO_HISTORY_TEMPLATE.format(
                    context_markdown=context_markdown,
                    question=question,
                )

        # 3. Create the parts for the current user turn
        current_turn_parts = []

        # Documents attached to THIS message, from the durable store
        if document_ids:
            current_turn_parts.extend(await build_parts(self, document_ids))

        # Legacy inline attachments (pre-document_ids clients)
        if attachments:
            for file in attachments:
                uri = file.uri if hasattr(file, 'uri') else file.get('uri')
                mime_type = file.mime_type if hasattr(file, 'mime_type') else file.get('mime_type')
                if uri and mime_type:
                    current_turn_parts.append(types.Part.from_uri(file_uri=uri, mime_type=mime_type))

        attached_count = len(document_ids or []) + len(attachments or [])
        if attached_count:
            final_prompt += (
                f"\n\n[System Note: The user has attached {attached_count} file(s) to this "
                f"message. Please analyze the provided file(s) to answer their question.]"
            )

        # Add the text prompt
        current_turn_parts.append(types.Part.from_text(text=final_prompt))

        # Append current user turn
        payload.append(types.Content(role="user", parts=current_turn_parts))
        
        logger.info(f"📤 Sending structured array ({len(payload)} messages) to Main LLM...")
        
        config = types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=self.custom_main_prompt or SYSTEM_PROMPT
        )

        response = await self._generate_with_retry(
            model=self.main_model,
            contents=payload,
            config=config
        )

        # response.text is None whenever the model returns no usable candidate (SAFETY,
        # RECITATION, MAX_TOKENS, ...). Calling .strip() on it raised a bare AttributeError
        # that surfaced to the user as a generic 500 with no clue what happened.
        answer = (response.text or "").strip()
        if not answer:
            raise EmptyLLMResponse(_finish_reason(response))

        logger.info(f"📥 Main LLM responded ({len(answer):,} chars)")

        return {
            "answer": answer,
            "model": self.main_model,
            "context_length": len(context_markdown),
            "prompt_length": len(final_prompt),
        }

    async def compare_documents(self, doc_parts: list, labels: list[str], language_hint: str = "") -> Dict[str, Any]:
        """Compares 2-4 documents and returns a structured, clause-by-clause diff.

        Uses response_mime_type=application/json with an explicit schema so the result
        can drive a real table instead of being prose the UI has to guess at.
        """
        schema = {
            "type": "object",
            "properties": {
                "documents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["id", "label"],
                    },
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clause": {"type": "string"},
                            "values": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "doc_id": {"type": "string"},
                                        "text": {"type": "string", "nullable": True},
                                    },
                                    "required": ["doc_id"],
                                },
                            },
                            "status": {"type": "string", "enum": ["match", "differs", "missing"]},
                            "severity": {"type": "string", "enum": ["info", "warn", "risk"]},
                            "note": {"type": "string", "nullable": True},
                        },
                        "required": ["clause", "values", "status", "severity"],
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["documents", "rows", "summary"],
        }

        instruction = "Compare the attached documents:\n" + "\n".join(
            f"- Document {chr(65 + i)}: {label}" for i, label in enumerate(labels)
        )
        if language_hint:
            instruction += f"\n\nRespond in: {language_hint}"

        parts = list(doc_parts) + [types.Part.from_text(text=instruction)]
        config = types.GenerateContentConfig(
            temperature=0.1,
            system_instruction=COMPARE_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=schema,
        )

        response = await self._generate_with_retry(
            model=self.main_model,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
        )

        raw = (response.text or "").strip()
        if not raw:
            raise EmptyLLMResponse(_finish_reason(response))
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Comparison returned non-JSON: {raw[:300]}")
            raise EmptyLLMResponse("MALFORMED_JSON") from e

    async def draft_contract(
        self, template_text: str, contract_type: str, answers: Dict[str, Any],
        legal_context: str = "", language: str = "uz",
    ) -> Dict[str, Any]:
        """Generates a contract as structured JSON, ready for the .docx renderer."""
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "intro": {"type": "string", "nullable": True},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "number": {"type": "integer"},
                            "heading": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["number", "heading", "body"],
                    },
                },
                "signature_blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "party_role": {"type": "string"},
                            "party_name": {"type": "string"},
                        },
                        "required": ["party_role", "party_name"],
                    },
                },
                "chat_note": {"type": "string"},
            },
            "required": ["title", "sections", "chat_note"],
        }

        details = "\n".join(f"- {k}: {v}" for k, v in (answers or {}).items() if v) or "(none supplied)"
        prompt = (
            f"Contract type: {contract_type}\n"
            f"Output language: {language}\n\n"
            f"## TEMPLATE SKELETON\n{template_text}\n\n"
            f"## USER-SUPPLIED DETAILS\n{details}\n"
        )
        if legal_context:
            prompt += f"\n## LEGAL CONTEXT FROM THE UZBEKISTAN DATABASE\n{legal_context}\n"
        prompt += (
            "\nDraft the complete contract. `chat_note` is a short friendly message to show "
            "next to the download link, mentioning anything the user still needs to fill in."
        )

        config = types.GenerateContentConfig(
            temperature=0.2,
            system_instruction=DRAFT_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=schema,
        )

        response = await self._generate_with_retry(
            model=self.main_model, contents=prompt, config=config,
        )

        raw = (response.text or "").strip()
        if not raw:
            raise EmptyLLMResponse(_finish_reason(response))
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Draft returned non-JSON: {raw[:300]}")
            raise EmptyLLMResponse("MALFORMED_JSON") from e

    async def summarize_archive(
        self,
        old_messages: str,
        previous_summary: str = "",
    ) -> str:
        """
        Uses gemma-4-31b-it to merge old messages into the existing archive summary.
        """
        if previous_summary:
            prompt = SUMMARY_USER_PROMPT_TEMPLATE.format(
                previous_summary=previous_summary,
                old_messages=old_messages,
            )
        else:
            prompt = SUMMARY_FIRST_MESSAGE_TEMPLATE.format(
                old_messages=old_messages,
            )

        config = types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=SUMMARY_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(include_thoughts=True)
        )
        
        response = await self._generate_with_retry(
            model=self.router_model,
            contents=prompt,
            config=config
        )

        final_text = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if not getattr(part, 'thought', False):
                    final_text += part.text
        else:
            final_text = response.text
            
        summary = final_text.strip()
        logger.info(f"📋 Archive Summary updated ({len(summary)} chars)")
        return summary

    async def generate_title(self, first_message: str) -> str:
        """Generates a short chat title from the user's first message."""
        response = await self._generate_with_retry(
            model=self.main_model,
            contents=first_message,
            config=types.GenerateContentConfig(
                system_instruction=TITLE_SYSTEM_PROMPT,
            ),
        )

        title = response.text.strip().replace('"', "").replace("'", "")
        return title[:255]

    async def extract_document_metadata(self, document_header: str) -> Dict[str, Any]:
        """
        Uses gemma-4-31b-it to extract structured JSON metadata from a markdown document header.
        """
        logger.info("🧠 Requesting metadata extraction from LLM...")
        
        config = types.GenerateContentConfig(
            temperature=0.1,
            system_instruction=METADATA_EXTRACTION_SYSTEM_PROMPT,
            response_mime_type="application/json",
        )
        
        response = await self._generate_with_retry(
            model=self.router_model,
            contents=document_header,
            config=config
        )
        
        try:
            metadata = json.loads(response.text)
            logger.info(f"✅ Extracted metadata: {metadata}")
            return metadata
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse JSON from LLM: {response.text}")
            return {
                "title": "Unknown",
                "doc_id": None,
                "doc_date": None,
                "act_type": "Unknown"
            }

_llm_client_instance = None


def reset_llm_client() -> None:
    """Invalidates the cached singleton so next call to get_llm_client() rebuilds it."""
    global _llm_client_instance
    _llm_client_instance = None


async def get_llm_client() -> GeminiClient:
    """Returns a cached singleton GeminiClient based on current system settings."""
    global _llm_client_instance
    if _llm_client_instance is not None:
        return _llm_client_instance

    from app.database.queries import get_setting
    main_model   = await get_setting("current_llm_model")   or "gemini-3.1-flash-lite"
    router_model = await get_setting("current_router_model") or "gemma-4-31b-it"

    override = (await get_setting("override_prompts_enabled") or "").lower() == "true"
    custom_main_prompt   = (await get_setting("custom_main_prompt"))   if override else None
    custom_router_prompt = (await get_setting("custom_router_prompt")) if override else None

    _llm_client_instance = GeminiClient(
        main_model=main_model,
        router_model=router_model,
        custom_main_prompt=custom_main_prompt or None,
        custom_router_prompt=custom_router_prompt or None,
    )
    return _llm_client_instance
