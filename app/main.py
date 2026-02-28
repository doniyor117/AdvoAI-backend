"""
main.py — FastAPI Application Entry Point

Bootstraps the FastAPI application, configures CORS middleware,
and mounts all route modules.

Usage:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

# Initialize FastAPI App
app = FastAPI(
    title="Basira Legal Chatbot API",
    description="Hybrid Parent-Child RAG backend for Uzbekistan legal system.",
    version="1.0.0",
)

# Configure CORS for the frontend React application
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update this in production to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routes (Modular) ──

from app.routes import health, chat, ingest

app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["Ingestion"])
