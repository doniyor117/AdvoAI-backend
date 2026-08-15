"""
config.py — Application Configuration

Uses Pydantic Settings to load and strictly validate environment variables.
Provides a central `settings` object used throughout the application.
Fails fast on application startup if required variables are missing or invalid.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Settings.
    Reads from the environment or a local .env file.
    All required fields (no default) raise a clear ValidationError on startup if missing.
    """

    # ── App Config ────────────────────────────────────────────
    ENVIRONMENT: str = "development"

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str  # Required — no default

    # ── Google AI ─────────────────────────────────────────────
    GOOGLE_API_KEY: str = ""  # Can be empty if GOOGLE_API_KEYS is used
    GOOGLE_API_KEYS: str = "" # Comma separated list of keys

    # ── Embedding Model ───────────────────────────────────────
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_DIMENSIONS: int = 1536

    # ── JWT Auth ──────────────────────────────────────────────
    JWT_SECRET_KEY: str  # Required — no default (see validator below)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 72

    # ── Email / Resend (Optional fallback to console) ────────────
    RESEND_API_KEY: str | None = None
    EMAILS_FROM_EMAIL: str = "noreply@advoai.uz"
    EMAILS_FROM_NAME: str = "AdvoAI Security"

    # ── Google OAuth ──────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # ── S3 / Cloudflare R2 Storage ────────────────────────────
    S3_ENDPOINT_URL: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_BUCKET_NAME: str | None = None

    # ── Usage Limits ──────────────────────────────────────────
    GUEST_MESSAGE_LIMIT: int = 3
    FREE_DAILY_LIMIT: int = 20

    # ── Web Search (optional; the toggle degrades to a no-op if unset) ─────
    TAVILY_API_KEY: str | None = None
    SERPER_API_KEY: str | None = None

    # ── CORS ──────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_secrets(self) -> "Settings":
        """Ensures production-critical secrets are not left as placeholder values."""
        if "change-me" in self.JWT_SECRET_KEY.lower():
            raise ValueError(
                "JWT_SECRET_KEY must not be a placeholder. "
                "Generate a real secret with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
            
        if not self.GOOGLE_API_KEY and not self.GOOGLE_API_KEYS:
            raise ValueError("Either GOOGLE_API_KEY or GOOGLE_API_KEYS must be set.")
            
        return self

    def get_google_api_keys(self) -> list[str]:
        """Returns a list of Google API keys by parsing GOOGLE_API_KEYS or GOOGLE_API_KEY."""
        keys_str = self.GOOGLE_API_KEYS or self.GOOGLE_API_KEY
        keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        
        if not keys:
            raise ValueError("No valid Google API keys found in configuration.")
            
        return keys


# Singleton settings object imported throughout the application
settings = Settings()
