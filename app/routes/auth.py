"""
auth.py — Authentication API Routes

Handles user registration, login, Google OAuth, and session management.
Uses JWT tokens stored in HTTP-only cookies.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, Request
from pydantic import BaseModel, EmailStr, Field

import bcrypt

from app.config import settings
from app.middleware import create_access_token, get_current_user
from app.database.queries import (
    create_user, get_user_by_email, get_user_by_google_id,
    update_last_login, get_user_by_id
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request/Response Models ──────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")
    full_name: str = Field(..., min_length=2)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from frontend


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    auth_provider: str
    email_verified: bool


# ── Helpers ──────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def _set_auth_cookie(response: Response, user_id: str) -> str:
    """Creates JWT and sets it as an HTTP-only cookie."""
    token = create_access_token({"sub": user_id})
    is_production = settings.ENVIRONMENT != "development"
    response.set_cookie(
        key="yurika_token",
        value=token,
        httponly=True,
        secure=is_production,
        samesite="none" if is_production else "lax",
        max_age=settings.JWT_EXPIRY_HOURS * 3600,
        path="/",
    )
    return token


def _user_to_response(user: dict) -> dict:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "full_name": user.get("full_name"),
        "role": user["role"],
        "auth_provider": user["auth_provider"],
        "email_verified": user.get("email_verified", False),
    }


# ── Routes ───────────────────────────────────────────────────

@router.post("/register")
def register(request: RegisterRequest, response: Response):
    """
    Register a new user with email and password.
    """
    # Check if email already exists
    existing = get_user_by_email(request.email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # Create user
    password_hash = _hash_password(request.password)
    user = create_user(
        email=request.email,
        password_hash=password_hash,
        full_name=request.full_name,
        auth_provider="email",
    )

    if not user:
        raise HTTPException(status_code=500, detail="Failed to create account. Please try again.")

    # Set auth cookie + return token in body
    token = _set_auth_cookie(response, str(user["id"]))
    update_last_login(str(user["id"]))

    logger.info(f"New user registered: {request.email}")

    return {
        "message": "Account created successfully.",
        "user": _user_to_response(user),
        "token": token,
    }


@router.post("/login")
def login(request: LoginRequest, response: Response):
    """
    Login with email and password.
    """
    user = get_user_by_email(request.email)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.get("password_hash"):
        raise HTTPException(
            status_code=401,
            detail="This account uses Google Sign-In. Please log in with Google."
        )

    if not _verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="Account is deactivated.")

    # Set auth cookie + return token in body
    token = _set_auth_cookie(response, str(user["id"]))
    update_last_login(str(user["id"]))

    logger.info(f"User logged in: {request.email}")

    return {
        "message": "Login successful.",
        "user": _user_to_response(user),
        "token": token,
    }


@router.post("/google")
def google_auth(request: GoogleAuthRequest, response: Response):
    """
    Authenticate via Google OAuth.
    Verifies the Google ID token, creates or links the user account.
    """
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured.")

    try:
        # Verify the Google ID token
        idinfo = id_token.verify_oauth2_token(
            request.credential,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )

        google_id = idinfo["sub"]
        email = idinfo.get("email", "")
        full_name = idinfo.get("name", "")

    except ValueError as e:
        logger.error(f"Google token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid Google credential.")

    # Check if user already exists by Google ID
    user = get_user_by_google_id(google_id)

    if not user:
        # Check if user exists by email (might have registered with email first)
        user = get_user_by_email(email)
        if user:
            # Link Google ID to existing account
            from app.database.queries import link_google_id
            link_google_id(str(user["id"]), google_id)
        else:
            # Create new user via Google
            user = create_user(
                email=email,
                full_name=full_name,
                auth_provider="google",
                google_id=google_id,
                email_verified=True,
            )

    if not user:
        raise HTTPException(status_code=500, detail="Failed to process Google sign-in.")

    token = _set_auth_cookie(response, str(user["id"]))
    update_last_login(str(user["id"]))

    logger.info(f"Google auth: {email}")

    return {
        "message": "Google sign-in successful.",
        "user": _user_to_response(user),
        "token": token,
    }


@router.post("/logout")
def logout(response: Response):
    """
    Clears the auth cookie.
    """
    response.delete_cookie("yurika_token", path="/")
    return {"message": "Logged out successfully."}


@router.get("/me")
async def get_me(request: Request):
    """
    Returns the current authenticated user's profile.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    return {"user": _user_to_response(user)}


class UpdateProfileRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)


@router.patch("/me")
async def update_me(request: Request, body: UpdateProfileRequest):
    """
    Updates the current user's profile (name only for now).
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    from app.database.queries import update_user_profile
    update_user_profile(str(user["id"]), full_name=body.full_name)

    # Fetch updated user
    updated = get_user_by_id(str(user["id"]))
    logger.info(f"User profile updated: {user['email']}")

    return {"message": "Profile updated.", "user": _user_to_response(updated)}
