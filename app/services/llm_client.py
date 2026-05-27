"""
llm_client.py — Gemini LLM Client

Sends the user's question + retrieved legal context to Google's
Gemini 2.5 Flash model and returns a structured legal answer.
Also handles rolling summary generation and chat title creation.

Uses the GOOGLE_API_KEY from .env.
"""

import logging
from typing import Dict, Any

from google import genai
from google.genai import types

from app.config import settings
from app.services.prompts import (
    SYSTEM_PROMPT,
    RAG_USER_PROMPT_TEMPLATE,
    RAG_USER_PROMPT_NO_HISTORY_TEMPLATE,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
    SUMMARY_FIRST_MESSAGE_TEMPLATE,
    TITLE_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


# ── Gemini Client ─────────────────────────────────────────────

class GeminiClient:
    """
    Wraps Google's Gemini API for legal question answering,
    rolling summary generation, and chat title creation.
    """

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError(
                "❌ GOOGLE_API_KEY not set. Add it to your .env file."
            )

        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.model_name = model_name

        logger.info(f"🤖 Gemini {self.model_name} initialized")

    def ask(
        self,
        question: str,
        context_markdown: str,
        conversation_summary: str = "",
    ) -> Dict[str, Any]:
        """
        Sends the user's question with legal context (and optional
        conversation summary) to Gemini.

        Args:
            question:             The user's legal question.
            context_markdown:     Full Markdown document(s) from RAG retrieval.
            conversation_summary: Rolling summary of prior conversation turns.

        Returns:
            Dictionary with 'answer' and usage metadata.
        """
        if conversation_summary:
            user_prompt = RAG_USER_PROMPT_TEMPLATE.format(
                context_markdown=context_markdown,
                conversation_summary=conversation_summary,
                question=question,
            )
        else:
            user_prompt = RAG_USER_PROMPT_NO_HISTORY_TEMPLATE.format(
                context_markdown=context_markdown,
                question=question,
            )

        logger.info(f"📤 Sending to Gemini ({len(user_prompt):,} chars)...")

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
            ),
        )

        answer = response.text
        logger.info(f"📥 Gemini responded ({len(answer):,} chars)")

        return {
            "answer": answer,
            "model": self.model_name,
            "context_length": len(context_markdown),
            "prompt_length": len(user_prompt),
        }

    def summarize(
        self,
        user_message: str,
        ai_response: str,
        previous_summary: str = "",
    ) -> str:
        """
        Generates an updated rolling summary incorporating the latest exchange.

        Args:
            user_message:     The user's message.
            ai_response:      AdvoAI's response.
            previous_summary: The current rolling summary (empty for first message).

        Returns:
            Updated summary string.
        """
        if previous_summary:
            user_prompt = SUMMARY_USER_PROMPT_TEMPLATE.format(
                previous_summary=previous_summary,
                user_message=user_message,
                ai_response=ai_response,
            )
        else:
            user_prompt = SUMMARY_FIRST_MESSAGE_TEMPLATE.format(
                user_message=user_message,
                ai_response=ai_response,
            )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SUMMARY_SYSTEM_PROMPT,
            ),
        )

        summary = response.text.strip()
        logger.info(f"📋 Summary updated ({len(summary)} chars)")
        return summary

    def generate_title(self, first_message: str) -> str:
        """
        Generates a short chat title from the user's first message.

        Args:
            first_message: The user's first message in the session.

        Returns:
            A short title string (max ~6 words).
        """
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=first_message,
            config=types.GenerateContentConfig(
                system_instruction=TITLE_SYSTEM_PROMPT,
            ),
        )

        title = response.text.strip().replace('"', "").replace("'", "")
        # Truncate to 255 chars just in case
        return title[:255]

from functools import lru_cache

@lru_cache(maxsize=1)
def get_llm_client() -> GeminiClient:
    """
    Returns a cached singleton GeminiClient based on the current system settings.
    """
    from app.database.queries import get_setting
    model_name = get_setting("current_llm_model") or "gemini-2.5-flash"
    return GeminiClient(model_name=model_name)
