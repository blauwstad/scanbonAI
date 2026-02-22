"""
ScanbonAI Test Configuration
============================
Complete conftest.py with fixtures for database, test client, factories,
and mock adapters for OCR and WhatsApp services.

Aligned with the actual DB schema (001_initial_schema.sql) and
API routes from src/api/client.ts.
"""

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

# ---------------------------------------------------------------------------
# Adjust imports below to match your actual FastAPI project layout:
#   from app.main import create_app
#   from app.db.session import get_async_session
#   from app.config import settings
#   from app.models import Base
# ---------------------------------------------------------------------------
from app.main import create_app
from app.db.session import get_async_session
from app.config import settings
from app.models import Base


# ========================== DATABASE FIXTURES ==============================

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://scanbonai_test:test@localhost:5432/scanbonai_test",
)

_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_async_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session")
async def setup_database():
    """Create all tables once per test session, drop them when done."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest_asyncio.fixture()
async def db_session(setup_database) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a transactional database session that rolls back after each test.
    Every test is fully isolated.
    """
    async with _async_session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ========================== APP / CLIENT FIXTURES ==========================

@pytest_asyncio.fixture()
async def app(db_session: AsyncSession):
    """Create a FastAPI application wired to the test DB session."""
    application = create_app()

    async def _override_session():
        yield db_session

    application.dependency_overrides[get_async_session] = _override_session
    return application


@pytest_asyncio.fixture()
async def client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async httpx test client that talks to the FastAPI app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac


@pytest_asyncio.fixture()
async def admin_client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Test client pre-authenticated as an admin user."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": "Bearer test-admin-token"},
    ) as ac:
        yield ac


# ========================== DATA FACTORIES =================================
# These produce dicts matching the actual DB schema tables.

class TenantFactory:
    """Factory for the `tenants` table."""

    @staticmethod
    def create(**overrides) -> dict[str, Any]:
        defaults = {
            "id": str(uuid.uuid4()),
            "name": "Cabinet Comptable Test BV",
            "settings_json": {
                "default_currency": "EUR",
                "default_language": "nl",
                "auto_approve_threshold": 0.95,
                "require_admin_review": True,
                "export_format": "csv",
            },
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        defaults.update(overrides)
        return defaults


class UserFactory:
    """Factory for the `users` table."""

    _counter = 0

    @classmethod
    def create(cls, **overrides) -> dict[str, Any]:
        cls._counter += 1
        defaults = {
            "id": str(uuid.uuid4()),
            "tenant_id": "tenant-001",
            "whatsapp_phone": f"+3160000{cls._counter:04d}",
            "display_name": f"Test User {cls._counter}",
            "role": "user",
            "auth_token": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        defaults.update(overrides)
        return defaults

    @classmethod
    def create_admin(cls, **overrides) -> dict[str, Any]:
        return cls.create(role="admin", **overrides)


class InvoiceFactory:
    """
    Factory for the `invoices` table and its related rows
    (quality_checks, ocr_results, extracted_data, user_corrections).
    """

    _counter = 0

    @classmethod
    def create(cls, **overrides) -> dict[str, Any]:
        cls._counter += 1
        now = datetime.now(timezone.utc)
        defaults = {
            "id": str(uuid.uuid4()),
            "tenant_id": "tenant-001",
            "user_id": "user-001",
            "file_path": f"/data/tenant-001/user-001/2026-02/inv_{cls._counter}.jpg",
            "file_hash": hashlib.sha256(f"file{cls._counter}".encode()).hexdigest(),
            "status": "extracted",
            "upload_source": "whatsapp",
            "whatsapp_message_id": f"wamid.test{cls._counter:06d}",
            "month_partition": "2026-02",
            "created_at": now,
            "updated_at": now,
        }
        defaults.update(overrides)
        return defaults

    @staticmethod
    def sample_extracted_json() -> dict:
        """Matches the ExtractedInvoiceMetadata schema (with FieldWithConfidence)."""
        return {
            "supplier": {
                "name": {"value": "Albert Heijn BV", "confidence": 0.95},
                "address": {"value": "Provincialeweg 11, 1506 MA Zaandam", "confidence": 0.88},
                "country": {"value": "NL", "confidence": 0.99},
                "vat_id": {"value": "NL002230884B01", "confidence": 0.72},
                "kvk_coc": {"value": "35012085", "confidence": 0.80},
                "iban": {"value": "NL91ABNA0417164300", "confidence": 0.65},
            },
            "invoice_number": {"value": "FA-2026-00142", "confidence": 0.98},
            "invoice_date": {"value": "2026-01-15", "confidence": 0.91},
            "due_date": {"value": "2026-02-15", "confidence": 0.85},
            "payment_terms": {"value": "Net 30", "confidence": 0.70},
            "currency": {"value": "EUR", "confidence": 0.99},
            "subtotal": {"value": 850.00, "confidence": 0.88},
            "vat_amount": {"value": 178.50, "confidence": 0.83},
            "vat_rate": {"value": 21.0, "confidence": 0.90},
            "total_amount": {"value": 1028.50, "confidence": 0.96},
            "line_items": [
                {
                    "description": {"value": "Office Supplies Box A", "confidence": 0.90},
                    "quantity": {"value": 10, "confidence": 0.95},
                    "unit_price": {"value": 45.00, "confidence": 0.92},
                    "vat_rate": {"value": 21.0, "confidence": 0.85},
                    "amount": {"value": 450.00, "confidence": 0.93},
                },
                {
                    "description": {"value": "Printer Paper A4 500 sheets", "confidence": 0.88},
                    "quantity": {"value": 20, "confidence": 0.97},
                    "unit_price": {"value": 20.00, "confidence": 0.91},
                    "vat_rate": {"value": 21.0, "confidence": 0.85},
                    "amount": {"value": 400.00, "confidence": 0.94},
                },
            ],
            "category_suggestion": {
                "category": "office_supplies",
                "confidence": 0.88,
            },
            "booking_suggestion": {
                "account_code": "4200",
                "cost_center": None,
                "tax_treatment": "input_vat_deductible",
                "confidence": 0.75,
            },
            "metadata": {
                "ocr_model": "deepseek-vl-7b",
                "extraction_version": "v1.0.0",
                "processing_timestamp": "2026-02-22T10:30:00Z",
            },
        }

    @staticmethod
    def sample_confidence_scores() -> dict:
        """Flat confidence scores map used in extracted_data.confidence_scores."""
        return {
            "supplier_name": 0.95,
            "supplier_address": 0.88,
            "supplier_vat_id": 0.72,
            "supplier_iban": 0.65,
            "invoice_number": 0.98,
            "invoice_date": 0.91,
            "due_date": 0.85,
            "subtotal": 0.88,
            "vat_amount": 0.83,
            "vat_rate": 0.90,
            "total_amount": 0.96,
            "line_items": 0.70,
        }

    @classmethod
    def create_quality_failed(cls, **overrides) -> dict[str, Any]:
        return cls.create(status="quality_failed", **overrides)

    @classmethod
    def create_with_correction(cls, **overrides) -> dict[str, Any]:
        data = cls.create(status="reviewed", **overrides)
        data["_correction"] = {
            "corrected_json": {
                "supplier_vat_id": "NL002230884B02",
                "subtotal": 860.00,
            },
            "diff_json": {
                "supplier_vat_id": {
                    "original": "NL002230884B01",
                    "corrected": "NL002230884B02",
                },
                "subtotal": {
                    "original": 850.00,
                    "corrected": 860.00,
                },
            },
        }
        return data


class QualityCheckFactory:
    """Factory for the `quality_checks` table."""

    @staticmethod
    def create_passing(invoice_id: str, **overrides) -> dict[str, Any]:
        defaults = {
            "id": str(uuid.uuid4()),
            "invoice_id": invoice_id,
            "blur_score": 12.5,
            "resolution_ok": True,
            "skew_angle": 1.2,
            "shadow_score": 0.15,
            "exposure_score": 0.85,
            "overall_pass": True,
            "failure_reasons": [],
            "checked_at": datetime.now(timezone.utc),
        }
        defaults.update(overrides)
        return defaults

    @staticmethod
    def create_failing(invoice_id: str, **overrides) -> dict[str, Any]:
        defaults = {
            "id": str(uuid.uuid4()),
            "invoice_id": invoice_id,
            "blur_score": 85.0,
            "resolution_ok": False,
            "skew_angle": 25.0,
            "shadow_score": 0.75,
            "exposure_score": 0.20,
            "overall_pass": False,
            "failure_reasons": ["BLUR", "LOW_CONTRAST", "TRUNCATED"],
            "checked_at": datetime.now(timezone.utc),
        }
        defaults.update(overrides)
        return defaults


@pytest.fixture
def invoice_factory():
    return InvoiceFactory


@pytest.fixture
def user_factory():
    return UserFactory


@pytest.fixture
def tenant_factory():
    return TenantFactory


@pytest.fixture
def quality_check_factory():
    return QualityCheckFactory


# ========================== MOCK ADAPTERS ==================================

@pytest.fixture
def mock_ocr_adapter():
    """
    Mock OCR adapter (DeepSeek VL) returning predictable extraction.
    """
    adapter = AsyncMock()
    adapter.extract.return_value = {
        "raw_text": "FACTUUR FA-2026-00142\nAlbert Heijn BV\n...",
        "model_name": "deepseek-vl-7b",
        "model_version": "v1.0.0",
        "processing_time_ms": 2340,
        "extracted_json": InvoiceFactory.sample_extracted_json(),
        "confidence_scores": InvoiceFactory.sample_confidence_scores(),
    }
    adapter.health_check.return_value = True
    return adapter


@pytest.fixture
def mock_ocr_adapter_timeout():
    """Mock OCR adapter that simulates a timeout."""
    adapter = AsyncMock()
    adapter.extract.side_effect = asyncio.TimeoutError("OCR service timeout")
    return adapter


@pytest.fixture
def mock_ocr_adapter_low_confidence():
    """Mock OCR adapter returning uniformly low-confidence results."""
    adapter = AsyncMock()
    scores = {k: 0.3 for k in InvoiceFactory.sample_confidence_scores()}
    adapter.extract.return_value = {
        "raw_text": "...",
        "model_name": "deepseek-vl-7b",
        "model_version": "v1.0.0",
        "processing_time_ms": 5200,
        "extracted_json": InvoiceFactory.sample_extracted_json(),
        "confidence_scores": scores,
    }
    return adapter


@pytest.fixture
def mock_whatsapp_api():
    """Mock WhatsApp Cloud API client."""
    api = AsyncMock()
    api.send_text_message.return_value = {"messages": [{"id": "wamid.reply001"}]}
    api.send_template_message.return_value = {"messages": [{"id": "wamid.reply002"}]}
    api.mark_as_read.return_value = {"success": True}
    api.download_media.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 1024  # fake JPEG
    return api


@pytest.fixture
def mock_queue():
    """Mock Redis/ARQ job queue."""
    queue = AsyncMock()
    queue.enqueue.return_value = "job-id-001"
    queue.get_job_status.return_value = "queued"
    return queue


@pytest.fixture
def mock_storage():
    """Mock file storage service (local disk / S3-compatible)."""
    storage = AsyncMock()
    storage.upload.return_value = "/data/tenant-001/user-001/2026-02/test_invoice.jpg"
    storage.generate_signed_url.return_value = "https://scanbonai.example.com/signed/abc123"
    storage.delete.return_value = True
    return storage


# ========================== WEBHOOK HELPERS ================================

WHATSAPP_WEBHOOK_SECRET = os.getenv(
    "WHATSAPP_WEBHOOK_SECRET", "test-webhook-secret-key"
)


def build_whatsapp_webhook_payload(
    message_id: str = "wamid.HBgLMjEyNjAwMDAwMDAxFQIAERgS",
    phone: str = "31600000001",
    media_id: str = "media-id-001",
    timestamp: str | None = None,
    mime_type: str = "image/jpeg",
) -> dict:
    """Build a realistic WhatsApp Cloud API webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "PHONE_NUMBER_ID",
                            },
                            "messages": [
                                {
                                    "from": phone,
                                    "id": message_id,
                                    "timestamp": timestamp
                                    or str(int(datetime.now(timezone.utc).timestamp())),
                                    "type": "image",
                                    "image": {
                                        "mime_type": mime_type,
                                        "sha256": hashlib.sha256(
                                            message_id.encode()
                                        ).hexdigest(),
                                        "id": media_id,
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def compute_webhook_signature(
    payload: bytes, secret: str = WHATSAPP_WEBHOOK_SECRET
) -> str:
    """Compute HMAC-SHA256 signature for webhook validation."""
    return "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()


@pytest.fixture
def webhook_payload():
    return build_whatsapp_webhook_payload()


@pytest.fixture
def webhook_headers():
    """Return a callable that generates signed headers for a given payload."""

    def _make(payload_bytes: bytes) -> dict:
        sig = compute_webhook_signature(payload_bytes)
        return {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        }

    return _make


# ========================== SIGNED LINK HELPERS ============================

@pytest.fixture
def valid_signed_token():
    """Generate a valid signed URL token matching the `signed_links` table."""
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(settings.SIGNING_KEY)
    return s.dumps(
        {
            "invoice_id": "inv-001",
            "tenant_id": "tenant-001",
            "user_id": "user-001",
            "link_type": "image_view",
        },
        salt="signed-link",
    )


@pytest.fixture
def expired_signed_token():
    """Fixture to represent an expired token (validation mock needed)."""
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(settings.SIGNING_KEY)
    return s.dumps(
        {
            "invoice_id": "inv-001",
            "tenant_id": "tenant-001",
            "user_id": "user-001",
            "link_type": "image_view",
        },
        salt="signed-link",
    )


# ========================== EVENT LOOP =====================================

@pytest.fixture(scope="session")
def event_loop():
    """Provide a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
