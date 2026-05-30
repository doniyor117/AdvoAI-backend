"""
main.py — FastAPI Application Entry Point

Bootstraps the FastAPI application, configures CORS middleware,
manages the database connection pool lifecycle, and mounts all route modules.

Usage:
    uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings

# ── Logging Configuration ─────────────────────────────────────

_log_level = logging.DEBUG if settings.ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan: Pool init/teardown ──────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages startup and shutdown of the database connection pool."""
    from app.database.connection import init_pool, close_pool
    logger.info("AdvoAI Backend starting up...")
    await init_pool()
    from app.services.api_keys import api_key_manager
    await api_key_manager.async_reload_keys()
    yield
    logger.info("AdvoAI Backend shutting down...")
    await close_pool()


# ── FastAPI App ───────────────────────────────────────────────

app = FastAPI(
    title="AdvoAI Legal Chatbot API",
    description="Hybrid Parent-Child RAG backend for Uzbekistan legal system.",
    version="3.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────

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

# ── Global Exception Handler ──────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."},
    )

# ── Root Endpoint ─────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "online", "service": "AdvoAI Legal Chatbot API", "version": "3.0.0"}

# ── API Routes ────────────────────────────────────────────────

from app.routes import health, chat, auth, sessions, admin, account, public

app.include_router(health.router,    prefix="/api/health",    tags=["Health"])
app.include_router(public.router,    prefix="/api/public",    tags=["Public"])
app.include_router(auth.router,      prefix="/api/auth",      tags=["Auth"])
app.include_router(account.router,   prefix="/api/account",   tags=["Account"])
app.include_router(chat.router,      prefix="/api/chat",      tags=["Chat"])
app.include_router(sessions.router,  prefix="/api/sessions",  tags=["Sessions"])
app.include_router(admin.router,     prefix="/api/admin",     tags=["Admin"])
