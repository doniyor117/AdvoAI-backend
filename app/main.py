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
    title="Yurika Legal Chatbot API",
    description="Hybrid Parent-Child RAG backend for Uzbekistan legal system.",
    version="2.0.0",
)

# Configure CORS — credentials require explicit origins (not "*")
_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://10.144.172.154:3000",
    # Add your production domain here
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routes (Modular) ──

from app.routes import health, chat, ingest, auth, sessions, admin

app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["Ingestion"])

