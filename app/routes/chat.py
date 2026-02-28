"""
chat.py — RAG Chat API Route

Receives a user's question, triggers the full Vector Search
and Parent Document Retrieval pipeline, asks Gemini entirely
based on the context, and returns the grounded answer.
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.rag_pipeline import retrieve_context
from app.services.llm_client import GeminiClient

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=5, description="The legal question to ask Basira.")
    top_k: int = Field(default=5, ge=1, le=10, description="Number of vector chunks to retrieve.")


@router.post("/")
def ask_basira(request: ChatRequest):
    """
    Core RAG chatbot endpoint.
    
    Returns the LLM's answer alongside the retrieved parent documents
    and search chunks for citation/verification on the frontend.
    """
    try:
        logger.info(f"Processing chat request: {request.question[:50]}...")
        
        # 1. Parent-Child retrieval pipeline
        rag_result = retrieve_context(
            question=request.question, 
            top_k=request.top_k
        )
        
        # Guard clause: no documents found
        if not rag_result["parent_documents"]:
            return {
                "answer": "I couldn't find any relevant legal documents in my database to answer this question. Please try rephrasing or asking about a different topic.",
                "sources": [],
                "chunks_used": 0
            }

        # 2. Generate answer with Gemini
        client = GeminiClient()
        llm_result = client.ask(
            question=rag_result["question"],
            context_markdown=rag_result["context_markdown"]
        )
        
        # 3. Format the response for the frontend UI
        # We strip out the heavy 1024-dim vectors before sending to the client,
        # leaving only the necessary metadata and citation text.
        safe_chunks = []
        for chunk in rag_result["matched_chunks"]:
            # Omit the full chunk array; return only similarity score and text
            safe_chunks.append({
                "chunk_id": chunk["chunk_id"],
                "parent_id": chunk["parent_id"],
                "text_snippet": chunk["text"][:300] + "...",  # Abbreviate chunk for UI preview
                "similarity": chunk["similarity"]
            })
            
        safe_docs = []
        for doc in rag_result["parent_documents"]:
            # Omit the giant `full_markdown` text, returning only title and ID
            # Frontend can fetch the full text in a separate detail view if needed
            safe_docs.append({
                "document_id": doc["id"],
                "title": doc["title"]
            })

        return {
            "answer": llm_result["answer"],
            "model_used": llm_result["model"],
            "sources": safe_docs,
            "citations": safe_chunks,
            "metadata": {
                "context_length_chars": llm_result["context_length"],
                "prompt_length_chars": llm_result["prompt_length"]
            }
        }
        
    except ValueError as ve:
        # e.g. Missing GOOGLE_API_KEY
        logger.error(f"Configuration error: {ve}")
        raise HTTPException(status_code=500, detail="Server configuration error. Ensure API keys are set.")
    except Exception as e:
        logger.error(f"Chat API error: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while processing your request.")
