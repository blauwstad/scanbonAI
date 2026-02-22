"""
Application configuration via Pydantic Settings.

All values are read from environment variables (or a .env file).
Sensitive defaults are intentionally absent so the app fails fast
in production when a required secret is missing.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object for ScanbonAI backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Database & Cache
    # ------------------------------------------------------------------
    DATABASE_URL: str = Field(
        ...,
        description=(
            "Async PostgreSQL DSN, e.g. "
            "postgresql+asyncpg://user:pass@host:5432/scanbonai"
        ),
    )
    REDIS_URL: str = Field(
        "redis://localhost:6379/0",
        description="Redis connection URL used for Celery broker and cache.",
    )

    # ------------------------------------------------------------------
    # WhatsApp Business API
    # ------------------------------------------------------------------
    WHATSAPP_API_TOKEN: str = Field(
        ..., description="Bearer token for the WhatsApp Cloud API."
    )
    WHATSAPP_VERIFY_TOKEN: str = Field(
        ..., description="Arbitrary string used to verify the webhook subscription."
    )
    WHATSAPP_PHONE_NUMBER_ID: str = Field(
        ..., description="Numeric ID of the registered WhatsApp phone number."
    )

    # ------------------------------------------------------------------
    # DeepSeek OCR
    # ------------------------------------------------------------------
    DEEPSEEK_OCR_API_KEY: str = Field(
        ..., description="API key for the DeepSeek OCR v2 service."
    )
    DEEPSEEK_OCR_API_URL: str = Field(
        "https://api.deepseek.com/v2/ocr",
        description="Base URL for the DeepSeek OCR endpoint.",
    )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY: str = Field(
        ...,
        description=(
            "HMAC secret used for session cookies and magic-link tokens. "
            "Must be at least 32 bytes of random data."
        ),
    )
    SIGNING_KEY: str = Field(
        ...,
        description=(
            "Separate secret used exclusively for signed image-access URLs "
            "so that key rotation can be scoped."
        ),
    )

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    STORAGE_PATH: str = Field(
        "/data/scanbonai/invoices",
        description="Root directory on disk where invoice images are stored.",
    )

    # ------------------------------------------------------------------
    # Signed URL behaviour
    # ------------------------------------------------------------------
    SIGNED_URL_EXPIRY_SECONDS: int = Field(
        86400,
        description="Lifetime of a signed image-access URL in seconds (default 24 h).",
    )

    # ------------------------------------------------------------------
    # Image quality
    # ------------------------------------------------------------------
    MAX_IMAGE_SIZE_MB: int = Field(
        10,
        description="Maximum accepted invoice image size in megabytes.",
    )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    LOG_LEVEL: str = Field(
        "INFO",
        description="Python log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}, got {v!r}")
        return upper


# Module-level singleton – import this everywhere.
settings = Settings()  # type: ignore[call-arg]
