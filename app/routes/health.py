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
def health_check():
    """
    Basic health check. Simply verifies the FastAPI server is reachable.
    """
    return {"status": "online", "message": "Basira API is running"}


@router.get("/db")
def database_health_check():
    """
    Deep health check. Attempts to connect to the PostgreSQL database.
    """
    status = "healthy"
    message = "Database connection successful."
    
    try:
        # get_connection() raises an exception if it fails to connect
        # or if DATABASE_URL is invalid/missing.
        conn = get_connection()
        conn.close()
    except Exception as e:
        status = "unhealthy"
        message = f"Database connection failed: {str(e)}"
        logger.error(message)
        
        return JSONResponse(
            status_code=503,
            content={"status": status, "message": message}
        )

    return {"status": status, "message": message}
