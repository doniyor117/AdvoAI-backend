"""
auth.py — Authentication API Routes

Handles user registration, login, Google OAuth, and session management.
Uses JWT tokens stored in HTTP-only cookies.
"""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Response, Request, Depends
from pydantic import BaseModel, EmailStr, Field

import bcrypt

from app.config import settings
from app.middleware import create_access_token, get_current_user, require_auth
from app.services.email import generate_otp, get_otp_expiry, send_registration_otp
from app.database.queries import (
    create_user, get_user_by_email, get_user_by_google_id,
    update_last_login, get_user_by_id,
    create_verification_code, get_verification_code, delete_verification_codes_by_email
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request/Response Models ──────────────────────────────────

class SendOTPRequest(BaseModel):
    email: EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")
    full_name: str = Field(..., min_length=2)
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    allow_data_collection: bool = True


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
    terms_accepted: bool


# ── Helpers ──────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def _set_auth_cookie(response: Response, user_id: str, role: str) -> str:
    """Creates JWT and sets it as an HTTP-only cookie."""
    token = create_access_token({"sub": user_id, "role": role})
    is_production = settings.ENVIRONMENT != "development"
    max_age_seconds = settings.JWT_EXPIRY_HOURS * 3600
    expires_datetime = datetime.now(timezone.utc) + timedelta(seconds=max_age_seconds)
    
    response.set_cookie(
        key="advoai_token",
        value=token,
        httponly=True,
        secure=is_production,
        samesite="none" if is_production else "lax",
        max_age=max_age_seconds,
        expires=expires_datetime.strftime("%a, %d %b %Y %H:%M:%S GMT"),
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
        "has_password": bool(user.get("password_hash")),
        "is_google_linked": bool(user.get("google_id")),
        "allow_data_collection": user.get("allow_data_collection", True),
        "terms_accepted": bool(user.get("terms_accepted_at")),
    }


# ── Routes ───────────────────────────────────────────────────

@router.post("/send-registration-otp")
async def send_registration_otp_route(request: SendOTPRequest):
    """
    Generates a 6-digit OTP and sends it to the user's email for registration.
    """
    # Check if email already exists
    existing = await get_user_by_email(request.email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    otp = generate_otp()
    expires_at = get_otp_expiry(minutes=15)
    
    await create_verification_code(request.email, otp, "registration", expires_at)
    success = await send_registration_otp(request.email, otp)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send verification email.")
        
    return {"message": "Verification code sent to email."}


@router.post("/register")
async def register(request: RegisterRequest, response: Response):
    """
    Register a new user with email, password, and OTP verification.
    """
    # Check if email already exists
    existing = await get_user_by_email(request.email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # Verify OTP
    code_record = await get_verification_code(request.email, request.otp, "registration")
    if not code_record:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

    # Create user
    password_hash = _hash_password(request.password)
    user = await create_user(
        email=request.email,
        password_hash=password_hash,
        full_name=request.full_name,
        auth_provider="email",
        allow_data_collection=request.allow_data_collection,
        terms_accepted_at=datetime.now(timezone.utc)
    )

    if not user:
        raise HTTPException(status_code=500, detail="Failed to create account. Please try again.")

    # Cleanup OTP
    await delete_verification_codes_by_email(request.email, "registration")

    # Set auth cookie + return token in body
    token = _set_auth_cookie(response, str(user["id"]), user.get("role", "guest"))
    await update_last_login(str(user["id"]))

    logger.info(f"New user registered: {request.email}")

    return {
        "message": "Account created successfully.",
        "user": _user_to_response(user),
        "token": token,
    }


class ConsentRequest(BaseModel):
    allow_data_collection: bool

@router.post("/submit-consent")
async def submit_consent(request: ConsentRequest, current_user=Depends(require_auth)):
    """
    Submits user consent for Terms of Service and Data Collection.
    Primarily used during the Google SSO consent gate.
    """
    from app.database.queries import update_user_consent, get_user_by_id
    
    # We implicitly consider calling this endpoint as accepting the terms
    terms_accepted_at = datetime.now(timezone.utc)
    
    await update_user_consent(
        user_id=str(current_user["id"]),
        allow_data_collection=request.allow_data_collection,
        terms_accepted_at=terms_accepted_at
    )
    
    # Refresh user object to return
    updated_user = await get_user_by_id(str(current_user["id"]))
    
    return {
        "message": "Consent successfully recorded.",
        "user": _user_to_response(updated_user)
    }


@router.post("/login")
async def login(request: LoginRequest, response: Response):
    """
    Login with email and password.
    """
    user = await get_user_by_email(request.email)

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
    token = _set_auth_cookie(response, str(user["id"]), user.get("role", "guest"))
    await update_last_login(str(user["id"]))

    logger.info(f"User logged in: {request.email}")

    return {
        "message": "Login successful.",
        "user": _user_to_response(user),
        "token": token,
    }


@router.post("/google")
async def google_auth(request: GoogleAuthRequest, response: Response):
    """
    Authenticate via Google OAuth.
    Verifies the Google ID token, creates or links the user account.
    """
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured.")

    try:
        import asyncio
        # Verify the Google ID token in a separate thread to prevent blocking
        idinfo = await asyncio.to_thread(
            id_token.verify_oauth2_token,
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
    user = await get_user_by_google_id(google_id)

    if not user:
        # Check if user exists by email (might have registered with email first)
        user = await get_user_by_email(email)
        if user:
            # Link Google ID to existing account
            from app.database.queries import link_google_id
            await link_google_id(str(user["id"]), google_id)
        else:
            # Create new user via Google
            user = await create_user(
                email=email,
                full_name=full_name,
                auth_provider="google",
                google_id=google_id,
                email_verified=True,
            )

    if not user:
        raise HTTPException(status_code=500, detail="Failed to process Google sign-in.")

    token = _set_auth_cookie(response, str(user["id"]), user.get("role", "guest"))
    await update_last_login(str(user["id"]))

    logger.info(f"Google auth: {email}")

    return {
        "message": "Google sign-in successful.",
        "user": _user_to_response(user),
        "token": token,
    }


@router.post("/logout")
async def logout(response: Response):
    """
    Clears the auth cookie.
    """
    response.delete_cookie("advoai_token", path="/")
    return {"message": "Logged out successfully."}


@router.get("/me")
async def get_me(request: Request, response: Response):
    """
    Returns the current authenticated user's profile.
    If the JWT token role is stale compared to the DB, it reissues the token.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    # Check if the token role matches the DB role, and reissue if not
    token = request.cookies.get("advoai_token")
    if not token:
        # Fallback: check Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    new_token = None
    if token:
        from app.middleware import decode_access_token
        payload = decode_access_token(token)
        if payload and payload.get("role") != user.get("role"):
            new_token = _set_auth_cookie(response, str(user["id"]), user.get("role", "free"))

    resp_data = {"user": _user_to_response(user)}
    if new_token:
        resp_data["token"] = new_token
    return resp_data


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
    await update_user_profile(str(user["id"]), full_name=body.full_name)

    # Fetch updated user
    updated = await get_user_by_id(str(user["id"]))
    logger.info(f"User profile updated: {user['email']}")

    return {"message": "Profile updated.", "user": _user_to_response(updated)}
