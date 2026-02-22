"""
ScanbonAI API Endpoint Tests
=============================
Five complete pytest test functions demonstrating patterns for the actual
ScanbonAI API surface:
  - POST /hook/whatsapp (webhook)
  - GET  /api/v1/invoices/{id}
  - PUT  /api/v1/invoices/{id}/corrections
  - GET  /api/v1/invoices/{id}/image (signed URL)
  - GET  /api/v1/admin/metrics

Aligned with:
  - DB schema: 001_initial_schema.sql
  - API routes: src/api/client.ts
  - Types: src/types/index.ts & invoice-metadata.ts
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.conftest import (
    WHATSAPP_WEBHOOK_SECRET,
    build_whatsapp_webhook_payload,
    compute_webhook_signature,
)


# ---------------------------------------------------------------------------
# 1. POST /hook/whatsapp -- WhatsApp webhook intake
# ---------------------------------------------------------------------------

class TestWhatsAppWebhook:
    """Tests for the WhatsApp webhook intake endpoint (/hook/whatsapp)."""

    @pytest.mark.asyncio
    async def test_valid_webhook_accepted(
        self, client: httpx.AsyncClient, webhook_headers
    ):
        """
        A properly signed webhook with an image message should:
        - Return 200
        - Insert a row into webhook_events
        - Enqueue an OCR job
        """
        payload = build_whatsapp_webhook_payload(
            message_id="wamid.unique001",
            phone="31600000001",
        )
        body = json.dumps(payload).encode()
        headers = webhook_headers(body)

        with patch(
            "app.services.whatsapp.enqueue_ocr_job", new_callable=AsyncMock
        ) as mock_q:
            mock_q.return_value = "job-001"
            response = await client.post(
                "/hook/whatsapp", content=body, headers=headers
            )

        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "accepted"
        mock_q.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_message_idempotent(
        self, client: httpx.AsyncClient, webhook_headers
    ):
        """
        Sending the same whatsapp_message_id twice should be idempotent.
        The webhook_events table has UNIQUE on whatsapp_message_id.
        """
        payload = build_whatsapp_webhook_payload(message_id="wamid.dup001")
        body = json.dumps(payload).encode()
        headers = webhook_headers(body)

        with patch(
            "app.services.whatsapp.enqueue_ocr_job", new_callable=AsyncMock
        ) as mock_q:
            mock_q.return_value = "job-001"
            resp1 = await client.post(
                "/hook/whatsapp", content=body, headers=headers
            )
            resp2 = await client.post(
                "/hook/whatsapp", content=body, headers=headers
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Only one job should be enqueued (dedup by whatsapp_message_id)
        assert mock_q.call_count == 1

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self, client: httpx.AsyncClient):
        """A webhook with a wrong HMAC-SHA256 signature must return 401."""
        payload = build_whatsapp_webhook_payload()
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
        }

        response = await client.post(
            "/hook/whatsapp", content=body, headers=headers
        )

        assert response.status_code == 401
        detail = response.json().get("detail", "").lower()
        assert "signature" in detail or "unauthorized" in detail

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, client: httpx.AsyncClient):
        """A webhook without the X-Hub-Signature-256 header must return 401."""
        payload = build_whatsapp_webhook_payload()
        body = json.dumps(payload).encode()

        response = await client.post(
            "/hook/whatsapp",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 2. GET /api/v1/invoices/{id} -- tenant-scoped retrieval
# ---------------------------------------------------------------------------

class TestInvoiceRetrieval:
    """Tests for invoice retrieval with tenant isolation."""

    @pytest.mark.asyncio
    async def test_own_invoice_visible(
        self, client: httpx.AsyncClient, invoice_factory
    ):
        """User can retrieve their own tenant's invoice."""
        inv = invoice_factory.create(tenant_id="tenant-001")

        with patch(
            "app.services.invoices.get_invoice_by_id",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = inv
            response = await client.get(
                f"/api/v1/invoices/{inv['id']}",
                headers={"Authorization": "Bearer token-tenant-001"},
            )

        assert response.status_code == 200
        assert response.json()["data"]["id"] == inv["id"]

    @pytest.mark.asyncio
    async def test_wrong_tenant_forbidden(
        self, client: httpx.AsyncClient, invoice_factory
    ):
        """Accessing another tenant's invoice must return 403."""
        inv = invoice_factory.create(tenant_id="tenant-002")

        with patch(
            "app.services.invoices.get_invoice_by_id",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = inv
            response = await client.get(
                f"/api/v1/invoices/{inv['id']}",
                headers={"Authorization": "Bearer token-tenant-001"},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_nonexistent_invoice_404(self, client: httpx.AsyncClient):
        """Requesting a non-existent invoice ID returns 404."""
        fake_id = str(uuid.uuid4())

        with patch(
            "app.services.invoices.get_invoice_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await client.get(
                f"/api/v1/invoices/{fake_id}",
                headers={"Authorization": "Bearer token-tenant-001"},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_cross_tenant_access(
        self, admin_client: httpx.AsyncClient, invoice_factory
    ):
        """An admin can access any tenant's invoice."""
        inv = invoice_factory.create(tenant_id="tenant-999")

        with patch(
            "app.services.invoices.get_invoice_by_id",
            new_callable=AsyncMock,
            return_value=inv,
        ):
            response = await admin_client.get(
                f"/api/v1/invoices/{inv['id']}"
            )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. PUT /api/v1/invoices/{id}/corrections
# ---------------------------------------------------------------------------

class TestCorrectionSubmission:
    """Tests for user-submitted corrections (user_corrections table)."""

    @pytest.mark.asyncio
    async def test_valid_correction_accepted(
        self, client: httpx.AsyncClient, invoice_factory
    ):
        """
        A valid correction payload creates a user_corrections row
        and computes diff_json.
        """
        inv = invoice_factory.create(tenant_id="tenant-001", status="extracted")
        correction_payload = {
            "corrected_metadata": {
                "supplier_tax_id": "NL002230884B02",
                "subtotal": 860.00,
            },
            "field_corrections": [
                {
                    "field_name": "supplier_tax_id",
                    "original_value": "NL002230884B01",
                    "corrected_value": "NL002230884B02",
                    "ai_confidence": 0.72,
                },
                {
                    "field_name": "subtotal",
                    "original_value": 850.00,
                    "corrected_value": 860.00,
                    "ai_confidence": 0.88,
                },
            ],
        }

        with (
            patch(
                "app.services.invoices.get_invoice_by_id",
                new_callable=AsyncMock,
                return_value=inv,
            ),
            patch(
                "app.services.invoices.apply_correction",
                new_callable=AsyncMock,
            ) as mock_apply,
        ):
            mock_apply.return_value = {
                "id": str(uuid.uuid4()),
                "invoice_id": inv["id"],
                "corrected_json": correction_payload["corrected_metadata"],
                "diff_json": {
                    "supplier_tax_id": {
                        "original": "NL002230884B01",
                        "corrected": "NL002230884B02",
                    },
                    "subtotal": {"original": 850.00, "corrected": 860.00},
                },
            }

            response = await client.put(
                f"/api/v1/invoices/{inv['id']}/corrections",
                json=correction_payload,
                headers={"Authorization": "Bearer token-tenant-001"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "diff_json" in data or "field_corrections" in data

    @pytest.mark.asyncio
    async def test_correction_wrong_tenant_forbidden(
        self, client: httpx.AsyncClient, invoice_factory
    ):
        """Correction by user from wrong tenant returns 403."""
        inv = invoice_factory.create(tenant_id="tenant-002")

        with patch(
            "app.services.invoices.get_invoice_by_id",
            new_callable=AsyncMock,
            return_value=inv,
        ):
            response = await client.put(
                f"/api/v1/invoices/{inv['id']}/corrections",
                json={
                    "corrected_metadata": {"subtotal": 900.00},
                    "field_corrections": [],
                },
                headers={"Authorization": "Bearer token-tenant-001"},
            )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 4. GET /api/v1/invoices/{id}/image -- signed image URL
# ---------------------------------------------------------------------------

class TestSignedImageURL:
    """Tests for the signed image URL endpoint."""

    @pytest.mark.asyncio
    async def test_valid_signed_url_returned(
        self, client: httpx.AsyncClient, invoice_factory
    ):
        """Valid request returns a time-limited signed URL for the image."""
        inv = invoice_factory.create(tenant_id="tenant-001")

        with (
            patch(
                "app.services.invoices.get_invoice_by_id",
                new_callable=AsyncMock,
                return_value=inv,
            ),
            patch(
                "app.services.signed_urls.generate_signed_url",
                new_callable=AsyncMock,
            ) as mock_sign,
        ):
            mock_sign.return_value = "https://scanbonai.example.com/signed/token123"

            response = await client.get(
                f"/api/v1/invoices/{inv['id']}/image",
                headers={"Authorization": "Bearer token-tenant-001"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert "signed_url" in data
        assert "scanbonai" in data["signed_url"]

    @pytest.mark.asyncio
    async def test_expired_signed_url_returns_403(
        self, client: httpx.AsyncClient
    ):
        """Accessing an expired signed link returns 403."""
        with patch(
            "app.services.signed_urls.validate_signed_token",
            new_callable=AsyncMock,
        ) as mock_validate:
            mock_validate.side_effect = Exception("SignatureExpired")
            response = await client.get(
                "/api/v1/signed/some-expired-token-here"
            )

        # Per US-11: expired URLs return 403 with user-friendly message
        assert response.status_code in (403, 410)


# ---------------------------------------------------------------------------
# 5. GET /api/v1/admin/metrics
# ---------------------------------------------------------------------------

class TestAdminMetrics:
    """Tests for the admin metrics endpoint."""

    @pytest.mark.asyncio
    async def test_admin_can_access_metrics(
        self, admin_client: httpx.AsyncClient
    ):
        """Admin user receives the full AdminMetrics response shape."""
        with patch(
            "app.services.metrics.get_admin_metrics",
            new_callable=AsyncMock,
        ) as mock_m:
            mock_m.return_value = {
                "summary": {
                    "total_invoices": 142,
                    "pending_review": 12,
                    "approved": 118,
                    "rejected": 5,
                    "unreadable": 7,
                    "accuracy_rate": 0.91,
                    "average_confidence": 0.87,
                    "average_processing_time_ms": 3200,
                },
                "accuracy_over_time": [],
                "field_corrections": [],
                "processing_volume": [],
            }
            response = await admin_client.get("/api/v1/admin/metrics")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "summary" in data
        assert data["summary"]["total_invoices"] == 142

    @pytest.mark.asyncio
    async def test_regular_user_rejected_from_admin(
        self, client: httpx.AsyncClient
    ):
        """Non-admin user is rejected from admin endpoints."""
        response = await client.get(
            "/api/v1/admin/metrics",
            headers={"Authorization": "Bearer token-tenant-001"},
        )

        assert response.status_code == 403
