"""
rate_limiter.py — Rate Limiting Service

Provides logic for usage rate limiting.
"""

import logging
import re
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException, Depends

from app.config import settings
from app.database.queries import (
    increment_usage, increment_guest_usage, get_setting,
    increment_upload_usage, increment_guest_upload_usage
)

logger = logging.getLogger(__name__)

# Valid fingerprint: hex string, 16–64 chars
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{16,64}$")

async def _get_dynamic_limit(key: str, fallback: int) -> int:
    """Reads a limit from the system_settings table, with .env fallback."""
    try:
        val = await get_setting(key)
        return int(val) if val is not None else fallback
    except Exception:
        return fallback

async def check_rate_limit(
    request: Request,
    user: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Checks and increments usage limits based on user role.

    Strategy: atomic UPSERT that increments and returns the new count in one
    query — eliminates the check-then-increment race condition.

    - Guest (no user): lifetime limit tracked by browser fingerprint
    - Free user: daily message limit
    - Admin: unlimited

    Limits are read from the system_settings DB table (admin-configurable),
    falling back to .env values.

    Raises 429 if limit exceeded.
    Raises 403 if user is banned.
    """
    if user:
        if user.get("is_banned"):
            raise HTTPException(
                status_code=403,
                detail="Your account has been suspended. Please contact support."
            )

        # Atomic increment — get new count after insert/update
        new_count = await increment_usage(user["id"])

        # If user is admin, they have no limits, so just return
        if user.get("role") == "admin":
            return user

        free_daily_limit = await _get_dynamic_limit("free_daily_limit", settings.FREE_DAILY_LIMIT)

        # Check after incrementing: if the new count exceeds limit, reject
        if new_count > free_daily_limit:
            raise HTTPException(
                status_code=429,
                detail=f"Daily message limit reached ({free_daily_limit}/day). "
                       f"Please try again tomorrow or upgrade your plan."
            )

        return user

    # Guest user — check lifetime limit via fingerprint
    fingerprint = request.headers.get("X-Fingerprint", "").strip().lower()

    if not fingerprint:
        raise HTTPException(
            status_code=400,
            detail="Browser fingerprint required for guest access."
        )

    if not _FINGERPRINT_RE.match(fingerprint):
        raise HTTPException(
            status_code=400,
            detail="Invalid fingerprint format."
        )

    guest_limit = await _get_dynamic_limit("guest_message_limit", settings.GUEST_MESSAGE_LIMIT)

    # Atomic increment — get new count after insert/update
    new_count = await increment_guest_usage(fingerprint)

    if new_count > guest_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Guest message limit reached ({guest_limit} messages). "
                   f"Please create a free account to continue."
        )

    return None

async def check_upload_limit(
    request: Request,
    user: Optional[Dict[str, Any]],
    upload_type: str
) -> None:
    """
    Checks and increments upload limits. upload_type is 'doc' or 'image'.
    """
    if user:
        if user.get("is_banned"):
            raise HTTPException(status_code=403, detail="Your account has been suspended.")
        
        if user.get("role") == "admin":
            return
            
        new_count = await increment_upload_usage(user["id"], upload_type)
        
        if upload_type == "image":
            limit = await _get_dynamic_limit("free_daily_image_limit", 10)
            if new_count > limit:
                raise HTTPException(status_code=429, detail=f"Daily image upload limit reached ({limit}/day).")
        else:
            limit = await _get_dynamic_limit("free_daily_doc_limit", 10)
            if new_count > limit:
                raise HTTPException(status_code=429, detail=f"Daily document upload limit reached ({limit}/day).")
        return

    # Guest user
    fingerprint = request.headers.get("X-Fingerprint", "").strip().lower()
    if not fingerprint or not _FINGERPRINT_RE.match(fingerprint):
        raise HTTPException(status_code=400, detail="Browser fingerprint required for guest access.")
        
    new_count = await increment_guest_upload_usage(fingerprint, upload_type)
    
    if upload_type == "image":
        limit = await _get_dynamic_limit("guest_image_limit", 2)
        if new_count > limit:
            raise HTTPException(status_code=429, detail=f"Guest image upload limit reached ({limit}). Please create a free account.")
    else:
        limit = await _get_dynamic_limit("guest_doc_limit", 2)
        if new_count > limit:
            raise HTTPException(status_code=429, detail=f"Guest document upload limit reached ({limit}). Please create a free account.")

