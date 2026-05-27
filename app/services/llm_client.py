"""
llm_client.py — Gemini & Gemma LLM Client

Handles RAG QA with Gemini 3.1 Pro/Flash, and Query Intent Routing 
& Summarization using Gemma 4 31B IT (with Thinking block parsing).
"""

import logging
import json
import re
import time
from typing import Dict, Any

from google import genai
from google.genai import types

from app.config import settings
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
)

logger = logging.getLogger(__name__)

class GeminiClient:
    """
    Wraps Google's GenAI API for legal QA, routing, and summarization.
    """

    def __init__(self, main_model: str = "gemini-3.1-flash-lite", router_model: str = "gemma-4-31b-it"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("❌ GOOGLE_API_KEY not set. Add it to your .env file.")

        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.main_model = main_model
        self.router_model = router_model

        logger.info(f"🤖 Main LLM initialized: {self.main_model}")
        logger.info(f"🧠 Router LLM initialized: {self.router_model}")

    def _generate_with_retry(self, model: str, contents: Any, config: Any, max_retries: int = 3):
        """Helper to execute generate_content with exponential backoff retries."""
        for attempt in range(max_retries):
            try:
                return self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"❌ LLM API failed after {max_retries} attempts: {e}")
                    raise
                wait_time = 2 ** attempt
                logger.warning(f"⚠️ LLM API error: {e}. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)

    def route_query(self, question: str) -> Dict[str, str]:
        """
        Uses gemma-4-31b-it to classify the query as 'conversational' or 'legal_rag'.
        Returns a dict: {"intent": "...", "search_query": "..."}
        """
        logger.info("🧠 Routing query intent...")
        
        config = types.GenerateContentConfig(
            system_instruction=ROUTER_SYSTEM_PROMPT,
            temperature=0.0,  # Zero temperature for strict instruction following
            thinking_config=types.ThinkingConfig(include_thoughts=True)
        )
        
        response = self._generate_with_retry(
            model=self.router_model,
            contents=question,
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

    def ask(
        self,
        question: str,
        structured_history: list[Dict[str, Any]] = None,
        context_markdown: str = "",
        session_summary: str = "",
        is_conversational: bool = False,
    ) -> Dict[str, Any]:
        """
        Answers the user's question. If is_conversational=True, ignores context_markdown.
        Uses structured_history for multi-turn conversational awareness natively.
        """
        payload = []

        # 1. Map prior history turns to Native Gemini Content objects
        if structured_history:
            for msg in structured_history:
                # Map our DB roles ('user', 'assistant') to Gemini roles ('user', 'model')
                role = "model" if msg.get("role") == "assistant" else "user"
                content_text = msg.get("content", "")
                payload.append(
                    {"role": role, "parts": [{"text": content_text}]}
                )

        # 2. Inject Context into the Current (Final) User Turn
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

        # Append user question to payload
        payload.append({
            "role": "user",
            "parts": [{"text": final_prompt}]
        })
        
        logger.info(f"📤 Sending structured array ({len(payload)} messages) to Main LLM...")
        
        config = types.GenerateContentConfig(
            temperature=0.3,
            system_instruction=SYSTEM_PROMPT
        )

        response = self._generate_with_retry(
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

    def summarize_archive(
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
        
        response = self._generate_with_retry(
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

    def generate_title(self, first_message: str) -> str:
        """Generates a short chat title from the user's first message."""
        response = self.client.models.generate_content(
            model=self.main_model,
            contents=first_message,
            config=types.GenerateContentConfig(
                system_instruction=TITLE_SYSTEM_PROMPT,
            ),
        )

        title = response.text.strip().replace('"', "").replace("'", "")
        return title[:255]

from functools import lru_cache

@lru_cache(maxsize=1)
def get_llm_client() -> GeminiClient:
    """Returns a cached singleton GeminiClient based on system settings."""
    from app.database.queries import get_setting
    # Allow fallback if setting doesn't exist
    main_model = get_setting("current_llm_model") or "gemini-3.1-flash-lite"
    router_model = "gemma-4-31b-it" # Hardcoded specialized router
    return GeminiClient(main_model=main_model, router_model=router_model)
