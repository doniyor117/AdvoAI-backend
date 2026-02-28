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
    # No default value here means Pydantic will throw a validation error
    # if DATABASE_URL is missing from the environment.
    DATABASE_URL: str
    
    # Google AI Config
    GOOGLE_API_KEY: str

    # Feature Toggles
    USE_RERANKER: bool = False

    # Pydantic v2 specific setting config
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore env vars not defined in the class
    )


# Instantiate a singleton settings object to be imported elsewhere
settings = Settings()
