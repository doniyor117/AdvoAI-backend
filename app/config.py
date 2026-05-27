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
    GOOGLE_API_KEY: str  # Required — no default

    # ── Embedding Model ───────────────────────────────────────
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_DIMENSIONS: int = 1536

    # ── JWT Auth ──────────────────────────────────────────────
    JWT_SECRET_KEY: str  # Required — no default (see validator below)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 72

    # ── Google OAuth ──────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # ── Usage Limits ──────────────────────────────────────────
    GUEST_MESSAGE_LIMIT: int = 3
    FREE_DAILY_LIMIT: int = 20

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
        return self


# Singleton settings object imported throughout the application
settings = Settings()
