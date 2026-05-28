import smtplib
import logging
import secrets
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

from app.config import settings

logger = logging.getLogger(__name__)

def generate_otp(length: int = 6) -> str:
    """Generates a secure numeric OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(length))

def get_otp_expiry(minutes: int = 15) -> datetime:
    """Returns the expiration timestamp for an OTP."""
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)

async def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Sends an email using SMTP if configured, otherwise prints to console.
    """
    if not (settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD):
        # Fallback to console for development
        logger.warning(f"\n{'='*50}\n📧 MOCK EMAIL TO: {to_email}\nSUBJECT: {subject}\n\n{body}\n{'='*50}\n")
        return True

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = f"{settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>"
    msg["To"] = to_email

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
            logger.info(f"Email sent successfully to {to_email}")
            return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False

async def send_registration_otp(to_email: str, otp: str) -> bool:
    subject = "Verify your AdvoAI Account"
    body = f"""Hello,

Your verification code to register for AdvoAI is: {otp}

This code will expire in 15 minutes. If you did not request this, please ignore this email.

Thanks,
The AdvoAI Team"""
    return await send_email(to_email, subject, body)

async def send_password_reset_otp(to_email: str, otp: str) -> bool:
    subject = "AdvoAI Password Reset Request"
    body = f"""Hello,

You requested a password reset for your AdvoAI account.
Your verification code is: {otp}

This code will expire in 15 minutes. If you did not request this, please ignore this email.

Thanks,
The AdvoAI Team"""
    return await send_email(to_email, subject, body)

async def send_email_change_otp(to_email: str, otp: str) -> bool:
    subject = "Verify your new email address for AdvoAI"
    body = f"""Hello,

You requested to change your email address.
Your verification code is: {otp}

This code will expire in 15 minutes. If you did not request this, please ignore this email.

Thanks,
The AdvoAI Team"""
    return await send_email(to_email, subject, body)
