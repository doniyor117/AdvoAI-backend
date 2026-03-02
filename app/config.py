"""
config.py — Application Configuration

Uses Pydantic Settings to load and strictly validate environment variables.
Provides a central `settings` object used throughout the application.
Fails fast on application startup if required variables are missing.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Settings. 
    Reads from the environment or a local .env file.
    """
    
    # App Config
    ENVIRONMENT: str = "development"
    
    # Database Config
    DATABASE_URL: str
    
    # Google AI Config
    GOOGLE_API_KEY: str

    # Feature Toggles
    USE_RERANKER: bool = False

    # ── JWT Auth ─────────────────────────────────────────────
    JWT_SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 72

    # ── Google OAuth ─────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # ── Usage Limits ─────────────────────────────────────────
    GUEST_MESSAGE_LIMIT: int = 3
    FREE_DAILY_LIMIT: int = 20

    # Pydantic v2 specific setting config
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instantiate a singleton settings object to be imported elsewhere
settings = Settings()

