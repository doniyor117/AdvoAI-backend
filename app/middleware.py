"""
middleware.py — Authentication & Rate Limiting Dependencies

FastAPI dependencies for JWT auth, role checks, and usage rate limiting.
Used via Depends() on route handlers.

Rate limiting uses atomic UPSERT queries — no read-then-write race conditions.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException, Depends
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

# Valid fingerprint: hex string, 16–64 chars
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{16,64}$")


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
    Extracts and validates JWT from Authorization header or cookies.
    Checks header first (for cross-domain/mobile), falls back to cookies.
    Returns user dict or None for unauthenticated (guest) users.
    Does NOT raise — returns None for guests.
    """
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        token = request.cookies.get("advoai_token")

    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    from app.database.queries import get_user_by_id
    user = await get_user_by_id(payload.get("sub"))
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

    if user.get("is_banned"):
        raise HTTPException(
            status_code=403,
            detail="Your account has been suspended. Please contact support."
        )

    return user


async def require_admin(request: Request) -> Dict[str, Any]:
    """
    Dependency that REQUIRES an admin or root_admin user.
    Raises 403 if user is not an admin or root_admin.
    """
    user = await require_auth(request)
    if user.get("role") not in ("admin", "root_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


async def require_root_admin(request: Request) -> Dict[str, Any]:
    """
    Dependency that REQUIRES a root_admin user.
    Root admins are the only accounts that can perform destructive or
    security-sensitive operations (deleting documents, bulk ingestion,
    promoting users, changing LLM models, modifying API keys).
    Root admin accounts can only be assigned directly in the database.
    Raises 403 if user is not root_admin.
    """
    user = await require_auth(request)
    if user.get("role") not in ("admin", "root_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    if user.get("role") != "root_admin":
        raise HTTPException(
            status_code=403,
            detail="Root admin access required. This operation can only be performed by a root administrator."
        )
    return user


# ── Rate Limiting ────────────────────────────────────────────

from app.services.rate_limiter import check_rate_limit

async def require_rate_limit(
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_current_user)
) -> Optional[Dict[str, Any]]:
    """Dependency wrapper for the rate_limiter service."""
    return await check_rate_limit(request, user)
