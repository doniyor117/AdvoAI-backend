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
)

logger = logging.getLogger(__name__)

class GeminiClient:
    """
    Wraps Google's GenAI API for legal QA, routing, and summarization.
    Uses ApiKeyManager to handle rate limits with multiple keys.
    """

    def __init__(self, main_model: str = "gemini-3.1-flash-lite", router_model: str = "gemma-4-31b-it"):
        self.main_model = main_model
        self.router_model = router_model

        logger.info(f"🤖 Main LLM initialized: {self.main_model}")
        logger.info(f"🧠 Router LLM initialized: {self.router_model}")

    @property
    def client(self):
        """Returns the dynamically active genai.Client from the ApiKeyManager."""
        return api_key_manager.get_current_client()

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

    async def route_query(self, question: str, recent_messages: list = None) -> Dict[str, str]:
        """
        Uses gemma-4-31b-it to classify the query as 'conversational' or 'legal_rag'.
        Returns a dict: {"intent": "...", "search_query": "..."}
        """
        logger.info("🧠 Routing query intent...")
        
        config = types.GenerateContentConfig(
            system_instruction=ROUTER_SYSTEM_PROMPT,
            temperature=0.0,  # Zero temperature for strict instruction following
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL")
        )
        
        prompt = question
        if recent_messages:
            history_text = "\n".join(
                f"{msg['role'].capitalize()}: {msg['content']}" 
                for msg in recent_messages
            )
            prompt = f"Recent Conversation History:\n{history_text}\n\nCurrent Query: {question}"

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
                return {"intent": intent, "search_query": search_query}
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
        attachments: list = None
    ) -> Dict[str, Any]:
        """
        Answers the user's question. If is_conversational=True, ignores context_markdown.
        Uses structured_history for multi-turn conversational awareness natively.
        Supports multimodal attachments via Google GenAI File URIs.
        """
        payload = []

        # 1. Map prior history turns to Native Gemini Content objects
        if structured_history:
            for msg in structured_history:
                # 'user' or 'assistant' -> 'user' or 'model' for Gemini
                role = "user" if msg["role"] == "user" else "model"
                payload.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))

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
        
        # Add any file attachments first
        if attachments:
            final_prompt += f"\n\n[System Note: The user has attached {len(attachments)} file(s) to this message. Please analyze the provided file(s) to answer their question.]"
            for file in attachments:
                uri = file.uri if hasattr(file, 'uri') else file.get('uri')
                mime_type = file.mime_type if hasattr(file, 'mime_type') else file.get('mime_type')
                if uri and mime_type:
                    current_turn_parts.append(types.Part.from_uri(file_uri=uri, mime_type=mime_type))
                    
        # Add the text prompt
        current_turn_parts.append(types.Part.from_text(text=final_prompt))

        # Append current user turn
        payload.append(types.Content(role="user", parts=current_turn_parts))
        
        logger.info(f"📤 Sending structured array ({len(payload)} messages) to Main LLM...")
        
        config = types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=SYSTEM_PROMPT
        )

        response = await self._generate_with_retry(
            model=self.main_model,
            contents=payload,
            config=config
        )

        answer = response.text.strip()
        logger.info(f"📥 Main LLM responded ({len(answer):,} chars)")

        return {
            "answer": answer,
            "model": self.main_model,
            "context_length": len(context_markdown),
            "prompt_length": len(final_prompt),
        }

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

async def get_llm_client() -> GeminiClient:
    """Returns a cached singleton GeminiClient based on system settings."""
    global _llm_client_instance
    if _llm_client_instance is not None:
        return _llm_client_instance

    from app.database.queries import get_setting
    # Allow fallback if setting doesn't exist
    main_model = await get_setting("current_llm_model") or "gemini-3.1-flash-lite"
    router_model = await get_setting("current_router_model") or "gemma-4-31b-it"
    
    _llm_client_instance = GeminiClient(main_model=main_model, router_model=router_model)
    return _llm_client_instance
