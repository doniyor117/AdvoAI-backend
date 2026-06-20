"""
account.py — Account Management API Routes

Handles password updates, email changes, and identity provider (Google) linking.
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field

from app.middleware import require_auth
from app.database.queries import (
    get_user_by_id, get_user_by_email, get_user_by_google_id,
    create_verification_code, get_verification_code, delete_verification_codes_by_email,
    update_user_password, update_user_email, link_google_id, unlink_google_id
)
from app.database.connection import get_connection
from app.services.email import (
    generate_otp, get_otp_expiry,
    send_password_reset_otp, send_email_change_otp
)
from app.routes.auth import _hash_password, _verify_password

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Models ───────────────────────────────────────────────────

class UpdatePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)

class UpdateAdminPasswordRequest(BaseModel):
    current_admin_password: str
    new_admin_password: str = Field(..., min_length=8)

class UpdatePrivacyRequest(BaseModel):
    allow_data_collection: bool

class RequestResetPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8)

class RequestEmailChangeRequest(BaseModel):
    new_email: EmailStr

class VerifyEmailChangeRequest(BaseModel):
    new_email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6)

class LinkGoogleRequest(BaseModel):
    credential: str  # Google ID token

# ── Routes ───────────────────────────────────────────────────

@router.post("/update-password")
async def update_password(request: UpdatePasswordRequest, user=Depends(require_auth)):
    """Update password for authenticated user."""
    db_user = await get_user_by_id(user["id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if not db_user.get("password_hash"):
        raise HTTPException(status_code=400, detail="User does not have a password set. Use reset password or set one.")

    if not _verify_password(request.current_password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect current password")

    new_hash = _hash_password(request.new_password)
    await update_user_password(user["id"], new_hash)
    return {"message": "Password updated successfully"}


@router.post("/update-admin-password")
async def update_admin_password(request: UpdateAdminPasswordRequest, user=Depends(require_auth)):
    """Updates a user's admin password."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can have admin passwords.")

    async with get_connection() as cur:
        await cur.execute("SELECT admin_password_hash FROM users WHERE id = %s", (user["id"],))
        row = await cur.fetchone()
        
    db_hash = row.get("admin_password_hash") if row else None
    
    if db_hash and not _verify_password(request.current_admin_password, db_hash):
        raise HTTPException(status_code=401, detail="Incorrect current admin password")

    new_hash = _hash_password(request.new_admin_password)
    async with get_connection() as cur:
        await cur.execute("UPDATE users SET admin_password_hash = %s WHERE id = %s", (new_hash, user["id"]))
        
    return {"message": "Admin password updated successfully"}

@router.post("/update-privacy")
async def update_privacy(request: UpdatePrivacyRequest, user=Depends(require_auth)):
    """Updates privacy settings."""
    async with get_connection() as cur:
        await cur.execute(
            "UPDATE users SET allow_data_collection = %s WHERE id = %s",
            (request.allow_data_collection, user["id"])
        )
    return {"message": "Privacy settings updated", "allow_data_collection": request.allow_data_collection}


@router.post("/request-password-reset")
async def request_password_reset(request: RequestResetPasswordRequest):
    """Sends OTP for password reset."""
    db_user = await get_user_by_email(request.email)
    if not db_user:
        # Don't leak whether the email exists, just return success
        return {"message": "If the email exists, a password reset code has been sent."}

    otp = generate_otp()
    expires_at = get_otp_expiry(minutes=15)
    await create_verification_code(request.email, otp, "password_reset", expires_at, user_id=db_user["id"])
    await send_password_reset_otp(request.email, otp)
    
    return {"message": "If the email exists, a password reset code has been sent."}


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """Verifies OTP and resets password."""
    code_record = await get_verification_code(request.email, request.otp, "password_reset")
    if not code_record:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

    db_user = await get_user_by_email(request.email)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    new_hash = _hash_password(request.new_password)
    await update_user_password(db_user["id"], new_hash)
    await delete_verification_codes_by_email(request.email, "password_reset")

    return {"message": "Password reset successfully"}


@router.post("/request-email-change")
async def request_email_change(request: RequestEmailChangeRequest, user=Depends(require_auth)):
    """Sends OTP to the *new* email address to verify it."""
    # Check if the new email is already in use
    existing = await get_user_by_email(request.new_email)
    if existing:
        raise HTTPException(status_code=409, detail="This email is already in use by another account.")

    otp = generate_otp()
    expires_at = get_otp_expiry(minutes=15)
    
    # Store verification code under the NEW email
    await create_verification_code(request.new_email, otp, "email_change", expires_at, user_id=user["id"])
    await send_email_change_otp(request.new_email, otp)
    
    return {"message": "Verification code sent to new email."}


@router.post("/verify-email-change")
async def verify_email_change(request: VerifyEmailChangeRequest, user=Depends(require_auth)):
    """Verifies OTP and changes the user's email."""
    code_record = await get_verification_code(request.new_email, request.otp, "email_change")
    if not code_record or str(code_record["user_id"]) != str(user["id"]):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")

    # Double check it isn't taken
    existing = await get_user_by_email(request.new_email)
    if existing:
        raise HTTPException(status_code=409, detail="This email is already in use.")

    await update_user_email(user["id"], request.new_email)
    await delete_verification_codes_by_email(request.new_email, "email_change")

    return {"message": "Email updated successfully"}


@router.post("/link-google")
async def link_google(request: LinkGoogleRequest, user=Depends(require_auth)):
    """Links a Google account using the Google ID Token."""
    from google.oauth2 import id_token
    from google.auth.transport import requests
    from app.config import settings

    try:
        idinfo = id_token.verify_oauth2_token(
            request.credential, requests.Request(), settings.GOOGLE_CLIENT_ID
        )
        google_id = idinfo["sub"]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid Google token: {e}")

    # Check if Google ID is already linked
    existing = await get_user_by_google_id(google_id)
    if existing:
        raise HTTPException(status_code=409, detail="This Google account is already linked to an AdvoAI account.")

    await link_google_id(user["id"], google_id)
    return {"message": "Google account linked successfully"}


@router.post("/unlink-google")
async def unlink_google(user=Depends(require_auth)):
    """Unlinks a Google account. Requires a password to be set."""
    db_user = await get_user_by_id(user["id"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    if not db_user.get("password_hash"):
        raise HTTPException(status_code=400, detail="You must set a password before unlinking your Google account.")

    await unlink_google_id(user["id"])
    return {"message": "Google account unlinked successfully"}
