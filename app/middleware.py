"""
middleware.py — Authentication & Rate Limiting Dependencies

FastAPI dependencies for JWT auth, role checks, and usage rate limiting.
Used via Depends() on route handlers.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException, Depends
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)


# ── JWT Helpers ──────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    """Creates a JWT access token with expiration."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRY_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decodes and validates a JWT. Returns payload or None."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ── Auth Dependencies ────────────────────────────────────────

async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Extracts and validates JWT from cookies.
    Returns user dict or None for unauthenticated (guest) users.
    Does NOT raise — returns None for guests.
    """
    token = request.cookies.get("yurika_token")
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    # Fetch fresh user data from DB
    from app.database.queries import get_user_by_id
    user = get_user_by_id(payload.get("sub"))
    if not user or not user.get("is_active"):
        return None

    return user


async def require_auth(request: Request) -> Dict[str, Any]:
    """
    Dependency that REQUIRES a valid authenticated user.
    Raises 401 if no valid JWT is present.
    Raises 403 if user is banned.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")

    # Block banned users
    if user.get("is_banned"):
        raise HTTPException(
            status_code=403,
            detail="Your account has been suspended. Please contact support."
        )

    return user


async def require_admin(request: Request) -> Dict[str, Any]:
    """
    Dependency that REQUIRES an admin user.
    Raises 403 if user is not an admin.
    """
    user = await require_auth(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


# ── Dynamic Limits ───────────────────────────────────────────

def _get_dynamic_limit(key: str, fallback: int) -> int:
    """Reads a limit from the system_settings table, with .env fallback."""
    try:
        from app.database.queries import get_setting
        val = get_setting(key)
        if val is not None:
            return int(val)
    except Exception:
        pass
    return fallback


# ── Rate Limiting ────────────────────────────────────────────

async def check_rate_limit(
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_current_user)
) -> Optional[Dict[str, Any]]:
    """
    Checks usage limits based on user role.
    
    - Guest (no user): lifetime limit tracked by browser fingerprint
    - Free user: daily message limit
    - Admin: unlimited
    
    Limits are read from the system_settings DB table (admin-configurable),
    falling back to .env values.
    
    Raises 429 if limit exceeded.
    Raises 403 if user is banned.
    """
    from app.database.queries import (
        get_guest_usage, increment_guest_usage,
        get_daily_usage, increment_usage
    )

    if user and user.get("role") == "admin":
        # Admins have no limits
        return user

    if user:
        # Block banned users
        if user.get("is_banned"):
            raise HTTPException(
                status_code=403,
                detail="Your account has been suspended. Please contact support."
            )

        # Registered user — check daily limit (from DB settings)
        free_daily_limit = _get_dynamic_limit("free_daily_limit", settings.FREE_DAILY_LIMIT)
        daily_count = get_daily_usage(user["id"])
        if daily_count >= free_daily_limit:
            raise HTTPException(
                status_code=429,
                detail=f"Daily message limit reached ({free_daily_limit}/day). "
                       f"Please try again tomorrow or upgrade your plan."
            )
        increment_usage(user["id"])
        return user
    
    # Guest user — check lifetime limit via fingerprint
    fingerprint = request.headers.get("X-Fingerprint", "")
    if not fingerprint:
        raise HTTPException(
            status_code=400,
            detail="Browser fingerprint required for guest access."
        )

    guest_limit = _get_dynamic_limit("guest_message_limit", settings.GUEST_MESSAGE_LIMIT)
    guest_count = get_guest_usage(fingerprint)
    if guest_count >= guest_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Guest message limit reached ({guest_limit} messages). "
                   f"Please create a free account to continue."
        )
    increment_guest_usage(fingerprint)
    return None
