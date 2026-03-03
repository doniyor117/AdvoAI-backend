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

# Configure CORS — reads from CORS_ORIGINS env variable (comma-separated)
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Root endpoint (required for HF Spaces health check) ──

@app.get("/")
def root():
    return {"status": "online", "service": "Yurika Legal Chatbot API"}

# ── API Routes (Modular) ──

from app.routes import health, chat, ingest, auth, sessions, admin

app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["Ingestion"])

