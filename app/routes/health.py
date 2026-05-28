"""
health.py — Health Check API Route

Provides endpoints to verify the backend is running and can connect
to the required external services (like Neon PostgreSQL).
"""

import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database.connection import get_connection

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
async def health_check():
    """
    Basic health check. Simply verifies the FastAPI server is reachable.
    """
    return {"status": "online", "message": "AdvoAI API is running"}


@router.get("/db")
async def database_health_check():
    """
    Deep health check. Attempts to connect to the PostgreSQL database.
    """
    status = "healthy"
    message = "Database connection successful."
    
    try:
        # get_cursor() borrows from the pool and automatically returns it.
        # It will raise an exception if it fails to connect.
        from contextlib import closing
        async with get_connection() as cur:
            await cur.execute("SELECT 1")
    except Exception as e:
        status = "unhealthy"
        message = f"Database connection failed: {str(e)}"
        logger.error(message)
        
        return JSONResponse(
            status_code=503,
            content={"status": status, "message": message}
        )

    return {"status": status, "message": message}
