"""
llm_client.py — Gemini LLM Client

Sends the user's question + retrieved legal context to Google's
Gemini 2.5 Flash model and returns a structured legal answer.

Uses the GOOGLE_API_KEY from .env.
"""

import os
from typing import Dict, Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load .env
load_dotenv()


# ── System Prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are Basira — a highly accurate, conversational legal assistant specializing in Uzbekistan law.

Your primary duty is to synthesize the provided legal context and answer the user's question clearly, concisely, and directly.
DO NOT simply copy-paste large blocks of the legal text or dump the entire document back to the user.

Rules:
1. Provide a direct, conversational answer to the user's question first.
2. Follow up with a short, easy-to-read bulleted list of the most critical legal points that support your answer.
3. Cite specific articles or sections naturally within your sentences (e.g., "According to Article 5...").
4. If the answer is not in the context, say so clearly — never make up legal information.
5. If the question is ambiguous, ask for clarification.
6. Respond in the exact same language as the user's question.
7. Format your response elegantly with Markdown (bolding key terms, using bullet points) to maximize readability for a non-lawyer.
8. Provide link to the original source of the doc at the end if present.
"""


# ── Gemini Client ─────────────────────────────────────────────

class GeminiClient:
    """
    Wraps Google's Gemini API for legal question answering.
    """

    def __init__(self, model_name: str = "gemini-2.5-flash"):
        api_key = os.getenv("GOOGLE_API_KEY")

        if not api_key:
            raise ValueError(
                "❌ GOOGLE_API_KEY not set. Add it to your .env file."
            )

        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.system_instruction = SYSTEM_PROMPT
        
        print(f"🤖 Gemini {self.model_name} initialized (New SDK)")

    def ask(self, question: str, context_markdown: str) -> Dict[str, Any]:
        """
        Sends the user's question with legal context to Gemini.

        Args:
            question:         The user's legal question.
            context_markdown: Full Markdown document(s) from RAG retrieval.

        Returns:
            Dictionary with 'answer' and 'usage' metadata.
        """
        # Build the prompt: context first, then the question
        user_prompt = f"""## Legal Document Context

{context_markdown}

---

## User's Question

{question}
"""

        print(f"📤 Sending to Gemini ({len(user_prompt):,} chars)...")

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=self.system_instruction,
            )
        )

        answer = response.text
        print(f"📥 Gemini responded ({len(answer):,} chars)")

        return {
            "answer": answer,
            "model": self.model_name,
            "context_length": len(context_markdown),
            "prompt_length": len(user_prompt),
        }


# ── Test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 Testing Gemini client...\n")

    client = GeminiClient()

    # Simple test without real context
    test_result = client.ask(
        question="What is Article 1 of the Civil Code?",
        context_markdown="# Civil Code\n\n## Article 1. Purpose\n\nThis code regulates civil legal relations."
    )

    print(f"\n📝 Answer:\n{test_result['answer']}")
