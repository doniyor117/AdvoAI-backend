"""
public.py — Public Configuration API Routes

Public endpoints for fetching UI copy and global settings
needed by the frontend before authentication.
"""

import logging
from fastapi import APIRouter
from typing import Dict

from app.database.queries import get_all_settings

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/settings")
async def get_public_settings() -> Dict[str, str]:
    """
    Returns public UI copy and settings required by the frontend.
    Only returns specific UI keys to prevent leaking internal configurations.
    """
    settings = await get_all_settings()
    
    public_keys = {
        "ui_support_email",
        "global_notification",
        "global_notification_type"
    }
    
    return {k: v for k, v in settings.items() if k in public_keys}
