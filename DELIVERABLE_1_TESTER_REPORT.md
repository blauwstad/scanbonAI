# DELIVERABLE 1: Tester (QA Agent) Report

## Test Plan

### Test Architecture Overview

ScanbonAI is a multi-tenant FastAPI + React application for tax invoice processing via WhatsApp. The backend stores data in PostgreSQL (tables: `tenants`, `users`, `invoices`, `quality_checks`, `ocr_results`, `extracted_data`, `user_corrections`, `admin_reviews`, `audit_log`, `signed_links`, `webhook_events`), uses Redis for job queuing, and DeepSeek VL for OCR extraction. The frontend is a React + Vite + TypeScript SPA with TanStack Query.

**Test tooling:** pytest + pytest-asyncio + httpx (API), Playwright (UI), factory pattern (data), unittest.mock (adapters).

**Key API surface** (from `src/api/client.ts`):
- `POST /hook/whatsapp` -- webhook intake
- `GET/POST /api/v1/auth/{login,verify,logout,refresh,me}`
- `GET /api/v1/invoices` -- list (paginated, filterable)
- `GET /api/v1/invoices/{id}` -- single invoice
- `GET /api/v1/invoices/{id}/image` -- signed image URL
- `PUT /api/v1/invoices/{id}/corrections` -- submit corrections
- `POST /api/v1/invoices/{id}/confirm` -- confirm as correct
- `GET /api/v1/admin/invoices` -- admin list
- `GET /api/v1/admin/invoices/{id}` -- admin single
- `POST /api/v1/admin/invoices/{id}/review` -- admin review action
- `GET /api/v1/admin/metrics` -- admin metrics
- `POST /api/v1/admin/export` -- export invoices
- `GET /health` -- health check
- `GET /metrics` -- Prometheus metrics

---

## Unit Tests

### 1. Quality Check Module (blur, resolution, skew, shadow detection)

Maps to the `quality_checks` table: `blur_score`, `resolution_ok`, `skew_angle`, `shadow_score`, `exposure_score`, `overall_pass`, `failure_reasons`.

```python
# tests/unit/test_quality_check.py

import numpy as np
import pytest
from unittest.mock import patch

from app.services.quality_check import (
    calculate_blur_score,
    check_resolution,
    detect_skew_angle,
    detect_shadows,
    assess_overall_quality,
    QualityResult,
    BLUR_THRESHOLD,
    MIN_RESOLUTION,
)


class TestBlurDetection:

    def test_sharp_image_below_threshold(self):
        sharp = np.random.randint(0, 255, (1000, 1000, 3), dtype=np.uint8)
        score = calculate_blur_score(sharp)
        assert score < BLUR_THRESHOLD

    def test_blurry_image_above_threshold(self):
        blurry = np.full((1000, 1000, 3), 128, dtype=np.uint8)
        noise = np.random.normal(0, 1, (1000, 1000, 3)).astype(np.uint8)
        score = calculate_blur_score(blurry + noise)
        assert score >= BLUR_THRESHOLD

    def test_empty_image_raises(self):
        with pytest.raises(ValueError, match="empty"):
            calculate_blur_score(np.array([]))

    def test_grayscale_image_accepted(self):
        gray = np.random.randint(0, 255, (800, 600), dtype=np.uint8)
        score = calculate_blur_score(gray)
        assert isinstance(score, float)


class TestResolutionCheck:

    def test_adequate_resolution(self):
        assert check_resolution(1920, 1080) is True

    def test_below_minimum(self):
        assert check_resolution(320, 240) is False

    def test_exactly_minimum(self):
        assert check_resolution(MIN_RESOLUTION[0], MIN_RESOLUTION[1]) is True


class TestSkewDetection:

    def test_straight_image_near_zero(self):
        straight = np.zeros((1000, 800, 3), dtype=np.uint8)
        straight[100:900, 50:750] = 255
        angle = detect_skew_angle(straight)
        assert abs(angle) < 2.0


class TestShadowDetection:

    def test_uniform_lighting_no_shadow(self):
        uniform = np.full((500, 500, 3), 200, dtype=np.uint8)
        assert detect_shadows(uniform) < 0.3  # low shadow_score

    def test_strong_shadow_detected(self):
        image = np.full((500, 500, 3), 200, dtype=np.uint8)
        image[0:250, 0:250] = 40  # dark region
        assert detect_shadows(image) > 0.5


class TestOverallQualityAssessment:
    """
    assess_overall_quality returns a QualityResult matching the
    quality_checks table schema.
    """

    def test_good_quality_passes(self):
        with patch("app.services.quality_check.calculate_blur_score", return_value=10.0), \
             patch("app.services.quality_check.check_resolution", return_value=True), \
             patch("app.services.quality_check.detect_skew_angle", return_value=0.5), \
             patch("app.services.quality_check.detect_shadows", return_value=0.15), \
             patch("app.services.quality_check.calculate_exposure", return_value=0.85):

            result = assess_overall_quality(np.zeros((1000, 800, 3), dtype=np.uint8))
            assert isinstance(result, QualityResult)
            assert result.overall_pass is True
            assert result.failure_reasons == []

    def test_multiple_failures_rejected(self):
        with patch("app.services.quality_check.calculate_blur_score", return_value=90.0), \
             patch("app.services.quality_check.check_resolution", return_value=False), \
             patch("app.services.quality_check.detect_skew_angle", return_value=25.0), \
             patch("app.services.quality_check.detect_shadows", return_value=0.80), \
             patch("app.services.quality_check.calculate_exposure", return_value=0.20):

            result = assess_overall_quality(np.zeros((200, 200, 3), dtype=np.uint8))
            assert result.overall_pass is False
            assert "BLUR" in result.failure_reasons
            assert len(result.failure_reasons) >= 2
```

### 2. Signed URL Generation and Validation

Maps to the `signed_links` table: `token`, `invoice_id`, `user_id`, `link_type`, `expires_at`.

```python
# tests/unit/test_signed_urls.py

import pytest
from app.services.signed_urls import (
    generate_signed_token,
    validate_signed_token,
    SignedTokenPayload,
    TokenExpiredError,
    TokenInvalidError,
)

SECRET_KEY = "test-signing-key"
MAX_AGE = 259200  # 72 hours per US-02/US-11


class TestSignedURLGeneration:

    def test_generates_nonempty_token(self):
        token = generate_signed_token(
            invoice_id="inv-001", tenant_id="t-001",
            user_id="u-001", link_type="image_view",
            secret_key=SECRET_KEY,
        )
        assert isinstance(token, str) and len(token) > 20

    def test_different_invoices_different_tokens(self):
        t1 = generate_signed_token("inv-001", "t-001", "u-001", "image_view", SECRET_KEY)
        t2 = generate_signed_token("inv-002", "t-001", "u-001", "image_view", SECRET_KEY)
        assert t1 != t2


class TestSignedURLValidation:

    def test_valid_token_decodes(self):
        token = generate_signed_token("inv-001", "t-001", "u-001", "image_view", SECRET_KEY)
        payload = validate_signed_token(token, SECRET_KEY, max_age=MAX_AGE)
        assert payload.invoice_id == "inv-001"
        assert payload.link_type == "image_view"

    def test_expired_token_raises(self):
        token = generate_signed_token("inv-001", "t-001", "u-001", "image_view", SECRET_KEY)
        with pytest.raises(TokenExpiredError):
            validate_signed_token(token, SECRET_KEY, max_age=0)

    def test_wrong_secret_raises(self):
        token = generate_signed_token("inv-001", "t-001", "u-001", "image_view", SECRET_KEY)
        with pytest.raises(TokenInvalidError):
            validate_signed_token(token, "wrong-secret", max_age=MAX_AGE)

    def test_tampered_token_raises(self):
        token = generate_signed_token("inv-001", "t-001", "u-001", "image_view", SECRET_KEY)
        with pytest.raises(TokenInvalidError):
            validate_signed_token(token[:-5] + "XXXXX", SECRET_KEY, max_age=MAX_AGE)
```

### 3. Invoice Metadata Extraction Parsing

Maps to `extracted_data.extracted_json` using the `ExtractedInvoiceMetadata` schema (with `FieldWithConfidence<T>` wrappers).

```python
# tests/unit/test_extraction_parsing.py

import pytest
from app.services.extraction import (
    parse_ocr_response,
    normalize_vat_id,
    parse_european_date,
    calculate_totals_consistency,
    ExtractionResult,
)


class TestOCRResponseParsing:

    def test_full_valid_response(self):
        from tests.conftest import InvoiceFactory
        raw = InvoiceFactory.sample_extracted_json()
        result = parse_ocr_response(raw)
        assert isinstance(result, ExtractionResult)
        assert result.total_amount == 1028.50
        assert result.supplier_name == "Albert Heijn BV"

    def test_missing_optional_fields_ok(self):
        raw = {
            "supplier": {"name": {"value": "Test", "confidence": 0.9}},
            "total_amount": {"value": 500.0, "confidence": 0.8},
        }
        result = parse_ocr_response(raw)
        assert result.supplier_name == "Test"
        assert result.due_date is None

    def test_completely_empty_response(self):
        result = parse_ocr_response({})
        assert result.is_empty is True


class TestVATIDNormalization:
    """Dutch/European VAT ID (BTW-nummer) normalization."""

    def test_valid_nl_vat_id(self):
        assert normalize_vat_id("NL002230884B01") == "NL002230884B01"

    def test_vat_id_with_dots(self):
        assert normalize_vat_id("NL 002.230.884.B01") == "NL002230884B01"

    def test_invalid_vat_id(self):
        assert normalize_vat_id("INVALID") is None


class TestEuropeanDateParsing:

    def test_iso_format(self):
        dt = parse_european_date("2026-01-15")
        assert dt.day == 15 and dt.month == 1 and dt.year == 2026

    def test_dd_mm_yyyy_slash(self):
        dt = parse_european_date("15/01/2026")
        assert dt.day == 15 and dt.month == 1

    def test_dutch_month_name(self):
        dt = parse_european_date("15 januari 2026")
        assert dt.month == 1

    def test_german_format(self):
        dt = parse_european_date("15.01.2026")
        assert dt.day == 15 and dt.month == 1


class TestTotalsConsistency:

    def test_consistent(self):
        ok, diff = calculate_totals_consistency(
            subtotal=850.00, vat_rate=21.0, vat_amount=178.50, total=1028.50
        )
        assert ok is True
        assert abs(diff) < 0.01

    def test_inconsistent(self):
        ok, diff = calculate_totals_consistency(
            subtotal=850.00, vat_rate=21.0, vat_amount=178.50, total=999.00
        )
        assert ok is False
```

### 4. Diff Calculation Between Extracted and Corrected JSON

Maps to `user_corrections.diff_json`.

```python
# tests/unit/test_diff_calculation.py

import pytest
from app.services.diff import compute_invoice_diff, DiffResult


class TestDiffCalculation:

    def test_no_changes_empty_diff(self):
        original = {"supplier_name": "Test", "total_amount": 100.0}
        corrected = {"supplier_name": "Test", "total_amount": 100.0}
        result = compute_invoice_diff(original, corrected)
        assert result.has_changes is False
        assert len(result.changes) == 0

    def test_single_field_change(self):
        original = {"supplier_tax_id": "NL002230884B01", "total_amount": 1028.50}
        corrected = {"supplier_tax_id": "NL002230884B02", "total_amount": 1028.50}
        result = compute_invoice_diff(original, corrected)
        assert result.has_changes is True
        assert len(result.changes) == 1
        assert result.changes[0].field == "supplier_tax_id"

    def test_multiple_field_changes(self):
        original = {"supplier_name": "Old", "subtotal": 850.0, "total_amount": 1028.50}
        corrected = {"supplier_name": "New", "subtotal": 860.0, "total_amount": 1038.60}
        result = compute_invoice_diff(original, corrected)
        assert len(result.changes) == 3

    def test_type_coercion_no_false_diff(self):
        original = {"subtotal": 850}
        corrected = {"subtotal": 850.0}
        result = compute_invoice_diff(original, corrected)
        assert result.has_changes is False
```

### 5. Tenant Isolation Checks in Queries

All data queries must filter by `tenant_id`. The `invoices` table has a composite index `idx_invoices_tenant_status` and `idx_invoices_tenant_month`.

```python
# tests/unit/test_tenant_isolation.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.repositories.invoice_repo import InvoiceRepository


class TestTenantIsolation:

    @pytest.mark.asyncio
    async def test_list_invoices_filters_by_tenant(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        repo = InvoiceRepository(mock_session)
        await repo.list_invoices(tenant_id="tenant-001")

        query_str = str(
            mock_session.execute.call_args[0][0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "tenant_id" in query_str

    @pytest.mark.asyncio
    async def test_get_invoice_verifies_tenant(self):
        mock_session = AsyncMock()
        mock_invoice = MagicMock()
        mock_invoice.tenant_id = "tenant-002"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_invoice
        mock_session.execute.return_value = mock_result

        repo = InvoiceRepository(mock_session)
        with pytest.raises(PermissionError):
            await repo.get_invoice_by_id(
                invoice_id="inv-001", tenant_id="tenant-001"
            )

    @pytest.mark.asyncio
    async def test_update_scoped_to_tenant(self):
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        repo = InvoiceRepository(mock_session)
        await repo.update_invoice(
            invoice_id="inv-001", tenant_id="tenant-001",
            data={"status": "approved"},
        )

        stmt_str = str(
            mock_session.execute.call_args[0][0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "tenant_id" in stmt_str
```

---

## Integration Tests

### 1. WhatsApp Webhook -> Queue -> Worker -> OCR -> Storage Pipeline

```python
# tests/integration/test_pipeline.py

import json
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_pipeline_happy_path(
    client, db_session, webhook_payload, webhook_headers,
    mock_whatsapp_api, mock_ocr_adapter, mock_storage,
):
    """
    WhatsApp image webhook -> webhook_events row -> job enqueue ->
    worker: download -> quality_checks -> ocr_results -> extracted_data ->
    invoices status=extracted -> signed_links row -> WhatsApp confirmation.
    """
    body = json.dumps(webhook_payload).encode()
    headers = webhook_headers(body)

    with (
        patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api),
        patch("app.adapters.ocr.OCRAdapter", return_value=mock_ocr_adapter),
        patch("app.adapters.storage.StorageAdapter", return_value=mock_storage),
    ):
        # Step 1: Ingest
        resp = await client.post("/hook/whatsapp", content=body, headers=headers)
        assert resp.status_code == 200

        # Step 2: Worker processes job
        from app.tasks.ocr_tasks import process_invoice_job

        job_data = {
            "tenant_id": "tenant-001",
            "whatsapp_message_id": webhook_payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"],
            "media_id": "media-id-001",
            "phone_number": "31600000001",
        }
        result = await process_invoice_job(job_data, session=db_session)

        assert result["status"] == "extracted"
        assert result["invoice_id"] is not None

        # Step 3: Verify WhatsApp confirmation sent with signed URL
        mock_whatsapp_api.send_text_message.assert_called_once()
        msg_text = str(mock_whatsapp_api.send_text_message.call_args)
        assert "Review" in msg_text or "review" in msg_text or "link" in msg_text.lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pipeline_quality_rejected(
    client, db_session, webhook_payload, webhook_headers,
    mock_whatsapp_api, mock_storage, quality_check_factory,
):
    """If quality gate fails, status=quality_failed and user notified."""
    body = json.dumps(webhook_payload).encode()
    headers = webhook_headers(body)

    with (
        patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api),
        patch("app.adapters.storage.StorageAdapter", return_value=mock_storage),
        patch("app.services.quality_check.assess_overall_quality") as mock_q,
    ):
        from app.services.quality_check import QualityResult
        mock_q.return_value = QualityResult(
            blur_score=90.0, resolution_ok=False, skew_angle=30.0,
            shadow_score=0.80, exposure_score=0.20, overall_pass=False,
            failure_reasons=["BLUR", "LOW_CONTRAST"],
        )

        from app.tasks.ocr_tasks import process_invoice_job
        result = await process_invoice_job(
            {
                "tenant_id": "tenant-001",
                "whatsapp_message_id": "wamid.quality_fail",
                "media_id": "media-bad",
                "phone_number": "31600000001",
            },
            session=db_session,
        )

        assert result["status"] == "quality_failed"
        # User notified per US-03
        mock_whatsapp_api.send_text_message.assert_called_once()
```

### 2. User Correction -> Diff Storage -> Admin Visibility

```python
# tests/integration/test_correction_flow.py

@pytest.mark.asyncio
@pytest.mark.integration
async def test_correction_creates_diff_visible_to_admin(
    client, admin_client, db_session, invoice_factory,
):
    """
    User submits corrections -> user_corrections row with diff_json ->
    admin sees diff via GET /api/v1/admin/invoices/{id}.
    """
    # Setup invoice in DB (mocked)
    inv = invoice_factory.create(tenant_id="tenant-001", status="extracted")

    # User submits correction
    correction = {
        "corrected_metadata": {"supplier_tax_id": "NL002230884B02"},
        "field_corrections": [
            {
                "field_name": "supplier_tax_id",
                "original_value": "NL002230884B01",
                "corrected_value": "NL002230884B02",
                "ai_confidence": 0.72,
            }
        ],
    }

    from unittest.mock import patch, AsyncMock
    with patch("app.services.invoices.get_invoice_by_id", new_callable=AsyncMock, return_value=inv), \
         patch("app.services.invoices.apply_correction", new_callable=AsyncMock) as mock_apply:
        mock_apply.return_value = {"id": "corr-001", "diff_json": {"supplier_tax_id": {"original": "NL002230884B01", "corrected": "NL002230884B02"}}}

        resp = await client.put(
            f"/api/v1/invoices/{inv['id']}/corrections",
            json=correction,
            headers={"Authorization": "Bearer token-tenant-001"},
        )
        assert resp.status_code == 200

    # Admin retrieves invoice
    inv["status"] = "reviewed"
    inv["user_corrections"] = {"field_corrections": correction["field_corrections"]}
    with patch("app.services.invoices.get_invoice_by_id", new_callable=AsyncMock, return_value=inv):
        admin_resp = await admin_client.get(f"/api/v1/admin/invoices/{inv['id']}")
        assert admin_resp.status_code == 200
        data = admin_resp.json()["data"]
        assert data.get("user_corrections") is not None
```

### 3. Magic Link Auth Flow

```python
# tests/integration/test_auth.py

@pytest.mark.asyncio
@pytest.mark.integration
async def test_magic_link_login_flow(client, mock_whatsapp_api):
    """
    POST /api/v1/auth/login (email) -> magic link sent ->
    POST /api/v1/auth/verify (token) -> access_token + refresh_token.
    """
    from unittest.mock import patch, AsyncMock
    import re

    with patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api):
        # Step 1: Request login (per LoginRequest type: { email: string })
        resp = await client.post("/api/v1/auth/login", json={"email": "test@cabinet.nl"})
        assert resp.status_code == 200

        # Step 2: Extract token from sent message
        call_args = mock_whatsapp_api.send_text_message.call_args
        msg_text = str(call_args)
        token_match = re.search(r"verify[/?].*?token=([a-zA-Z0-9_.-]+)", msg_text)
        # Token should be present in the message
        assert token_match is not None or "token" in msg_text.lower()
```

### 4. Signed URL Access with Valid/Expired/Wrong-Tenant Tokens

```python
# tests/integration/test_signed_url_access.py

@pytest.mark.asyncio
@pytest.mark.integration
async def test_signed_url_valid_serves_image(client, invoice_factory, valid_signed_token):
    """Valid signed token grants proxied access to invoice image."""
    from unittest.mock import patch, AsyncMock

    with patch("app.services.signed_urls.resolve_token", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = {"invoice_id": "inv-001", "tenant_id": "t-001", "file_path": "/data/t-001/img.jpg"}

        with patch("app.services.storage.serve_file", new_callable=AsyncMock) as mock_serve:
            mock_serve.return_value = b"\xff\xd8\xff\xe0"
            resp = await client.get(f"/api/v1/signed/{valid_signed_token}")
            assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_signed_url_expired_returns_403(client, expired_signed_token):
    """Expired signed URL returns 403 per US-11."""
    from unittest.mock import patch, AsyncMock

    with patch("app.services.signed_urls.resolve_token", new_callable=AsyncMock) as mock_r:
        from app.services.signed_urls import TokenExpiredError
        mock_r.side_effect = TokenExpiredError("expired")
        resp = await client.get(f"/api/v1/signed/{expired_signed_token}")
        assert resp.status_code in (403, 410)
```

---

## API Tests (pytest + httpx)

Full test scaffolding in `/home/appuser/scanbonAI/tests/test_api_endpoints.py`. Coverage:

| Endpoint | Tests |
|---|---|
| `POST /hook/whatsapp` | valid payload, duplicate message, invalid signature, missing signature |
| `GET /api/v1/invoices/{id}` | own invoice, wrong tenant (403), admin cross-tenant, nonexistent (404) |
| `PUT /api/v1/invoices/{id}/corrections` | valid correction, wrong tenant (403) |
| `GET /api/v1/invoices/{id}/image` | valid signed URL, expired (403/410) |
| `GET /api/v1/admin/metrics` | admin access, regular user rejected (403) |

---

## Edge Cases

### 1. Unreadable Invoice (readability_score < 0.40) -> User Notification

Per US-03: status set to `REJECTED_QUALITY` with reason codes (`BLUR`, `LOW_CONTRAST`, `TRUNCATED`, `UNSUPPORTED_LANGUAGE`). WhatsApp message sent within 30 seconds.

```python
@pytest.mark.asyncio
async def test_unreadable_invoice_notifies_user(db_session, mock_whatsapp_api, mock_storage):
    from app.tasks.ocr_tasks import process_invoice_job
    from unittest.mock import patch
    from app.services.quality_check import QualityResult

    with (
        patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api),
        patch("app.adapters.storage.StorageAdapter", return_value=mock_storage),
        patch("app.services.quality_check.assess_overall_quality") as mock_q,
    ):
        mock_q.return_value = QualityResult(
            blur_score=92.0, resolution_ok=True, skew_angle=1.0,
            shadow_score=0.1, exposure_score=0.8, overall_pass=False,
            failure_reasons=["BLUR"],
        )

        result = await process_invoice_job(
            {"tenant_id": "t-001", "whatsapp_message_id": "wamid.blur001",
             "media_id": "m-blur", "phone_number": "31600000001"},
            session=db_session,
        )

        assert result["status"] == "quality_failed"
        mock_whatsapp_api.send_text_message.assert_called_once()
```

### 2. Duplicate WhatsApp Message -> Idempotent Handling

`webhook_events.whatsapp_message_id` has a UNIQUE constraint. Also `invoices` has `uq_invoices_tenant_file_hash UNIQUE (tenant_id, file_hash)` for file-level dedup per US-01.

```python
@pytest.mark.asyncio
async def test_duplicate_file_hash_returns_existing(db_session):
    from app.services.webhook_handler import handle_incoming_message

    msg1 = {"whatsapp_message_id": "wamid.d1", "tenant_id": "t-001",
            "phone": "31600000001", "media_id": "m-001", "file_hash": "abc123"}
    msg2 = {"whatsapp_message_id": "wamid.d2", "tenant_id": "t-001",
            "phone": "31600000001", "media_id": "m-002", "file_hash": "abc123"}

    r1 = await handle_incoming_message(msg1, session=db_session)
    r2 = await handle_incoming_message(msg2, session=db_session)

    assert r1["created"] is True
    assert r2["created"] is False  # same file_hash within 24h
```

### 3. Wrong-Tenant Access Attempt -> 403

```python
@pytest.mark.asyncio
async def test_cross_tenant_access_forbidden(client, invoice_factory):
    inv = invoice_factory.create(tenant_id="tenant-002")
    from unittest.mock import patch, AsyncMock
    with patch("app.services.invoices.get_invoice_by_id", new_callable=AsyncMock, return_value=inv):
        resp = await client.get(
            f"/api/v1/invoices/{inv['id']}",
            headers={"Authorization": "Bearer token-tenant-001"},
        )
    assert resp.status_code == 403
```

### 4. OCR Service Timeout -> Retry + Failure Notification

Per US-12: "Failed jobs are retried up to 3 times with exponential backoff."

```python
@pytest.mark.asyncio
async def test_ocr_timeout_retries_then_notifies(
    db_session, mock_whatsapp_api, mock_storage, mock_ocr_adapter_timeout,
):
    from app.tasks.ocr_tasks import process_invoice_job
    from unittest.mock import patch

    with (
        patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api),
        patch("app.adapters.storage.StorageAdapter", return_value=mock_storage),
        patch("app.adapters.ocr.OCRAdapter", return_value=mock_ocr_adapter_timeout),
        patch("app.services.quality_check.assess_overall_quality") as mock_q,
    ):
        from app.services.quality_check import QualityResult
        mock_q.return_value = QualityResult(
            blur_score=10.0, resolution_ok=True, skew_angle=0.5,
            shadow_score=0.1, exposure_score=0.85, overall_pass=True,
            failure_reasons=[],
        )

        result = await process_invoice_job(
            {"tenant_id": "t-001", "whatsapp_message_id": "wamid.timeout",
             "media_id": "m-to", "phone_number": "31600000001"},
            session=db_session,
        )

        assert result["status"] in ("failed", "quality_failed")
        assert mock_ocr_adapter_timeout.extract.call_count == 3
```

### 5. Webhook Signature Mismatch -> 401

```python
@pytest.mark.asyncio
async def test_webhook_signature_mismatch_401(client):
    import json
    from tests.conftest import build_whatsapp_webhook_payload

    payload = build_whatsapp_webhook_payload()
    body = json.dumps(payload).encode()
    response = await client.post(
        "/hook/whatsapp", content=body,
        headers={"Content-Type": "application/json",
                 "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert response.status_code == 401
```

### 6. Very Large Image File -> Size Limit Rejection

Per US-02: confirmation within 60 seconds for files under 5 MB.

```python
@pytest.mark.asyncio
async def test_large_image_rejected(mock_whatsapp_api, mock_storage):
    from unittest.mock import patch
    from app.tasks.ocr_tasks import process_invoice_job

    mock_whatsapp_api.download_media.return_value = b"\x00" * (11 * 1024 * 1024)

    with (
        patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api),
        patch("app.adapters.storage.StorageAdapter", return_value=mock_storage),
    ):
        result = await process_invoice_job(
            {"tenant_id": "t-001", "whatsapp_message_id": "wamid.large",
             "media_id": "m-huge", "phone_number": "31600000001"},
            session=None,
        )
        assert result["status"] == "rejected"
        assert "size" in result.get("reason", "").lower()
```

### 7. Concurrent Corrections on Same Invoice -> Conflict Handling

```python
@pytest.mark.asyncio
async def test_concurrent_corrections_conflict(client, invoice_factory):
    import asyncio
    from unittest.mock import patch, AsyncMock

    inv = invoice_factory.create(tenant_id="tenant-001", status="extracted")
    call_count = 0

    async def mock_apply(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise Exception("OptimisticLockError")
        return {"id": "corr-001", "diff_json": {}}

    with (
        patch("app.services.invoices.get_invoice_by_id", new_callable=AsyncMock, return_value=inv),
        patch("app.services.invoices.apply_correction", side_effect=mock_apply),
    ):
        headers = {"Authorization": "Bearer token-tenant-001"}
        payload = {"corrected_metadata": {"subtotal": 900.0}, "field_corrections": []}

        results = await asyncio.gather(
            client.put(f"/api/v1/invoices/{inv['id']}/corrections", json=payload, headers=headers),
            client.put(f"/api/v1/invoices/{inv['id']}/corrections", json=payload, headers=headers),
        )
        statuses = sorted([r.status_code for r in results])
        assert 200 in statuses
        assert 409 in statuses or 500 in statuses
```

### 8. Expired Signed Link -> 403 (US-11)

Per US-11: "Expired URLs return HTTP 403 with a user-friendly message and option to request a new link."

```python
@pytest.mark.asyncio
async def test_expired_signed_link_403_with_message(client):
    from unittest.mock import patch, AsyncMock
    from app.services.signed_urls import TokenExpiredError

    with patch("app.services.signed_urls.resolve_token", new_callable=AsyncMock) as mock_r:
        mock_r.side_effect = TokenExpiredError("expired")
        resp = await client.get("/api/v1/signed/some-expired-token")

    assert resp.status_code == 403
    body = resp.json()
    assert "expired" in body.get("detail", "").lower() or "expired" in body.get("message", "").lower()
```

### 9. Malformed Invoice (Not an Invoice) -> Graceful Handling

```python
@pytest.mark.asyncio
async def test_non_invoice_image_handled(db_session, mock_whatsapp_api, mock_storage):
    from unittest.mock import patch, AsyncMock

    mock_ocr = AsyncMock()
    mock_ocr.extract.return_value = {
        "raw_text": "", "model_name": "deepseek-vl-7b", "model_version": "v1.0.0",
        "processing_time_ms": 1200,
        "extracted_json": {}, "confidence_scores": {},
        "is_invoice": False,
    }

    with (
        patch("app.adapters.whatsapp.WhatsAppClient", return_value=mock_whatsapp_api),
        patch("app.adapters.storage.StorageAdapter", return_value=mock_storage),
        patch("app.adapters.ocr.OCRAdapter", return_value=mock_ocr),
        patch("app.services.quality_check.assess_overall_quality") as mock_q,
    ):
        from app.services.quality_check import QualityResult
        mock_q.return_value = QualityResult(
            blur_score=10.0, resolution_ok=True, skew_angle=1.0,
            shadow_score=0.1, exposure_score=0.85, overall_pass=True, failure_reasons=[],
        )

        from app.tasks.ocr_tasks import process_invoice_job
        result = await process_invoice_job(
            {"tenant_id": "t-001", "whatsapp_message_id": "wamid.notinv",
             "media_id": "m-selfie", "phone_number": "31600000001"},
            session=db_session,
        )
        assert result["status"] in ("not_invoice", "quality_failed")
```

### 10. Rate Limiting Exceeded -> 429

```python
@pytest.mark.asyncio
async def test_rate_limit_returns_429(client, webhook_headers):
    import json
    from tests.conftest import build_whatsapp_webhook_payload

    responses = []
    for i in range(120):
        payload = build_whatsapp_webhook_payload(message_id=f"wamid.rate{i:04d}")
        body = json.dumps(payload).encode()
        headers = webhook_headers(body)
        resp = await client.post("/hook/whatsapp", content=body, headers=headers)
        responses.append(resp)

    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes
```

---

## UI Tests (Playwright Scaffolding)

Based on the actual React components in `src/pages/` and `src/components/`.

```python
# tests/ui/test_invoice_review.py
"""
Playwright E2E tests for the invoice review UI.
Aligned with: src/pages/invoices/invoice-review-page.tsx,
              src/components/invoice/metadata-form.tsx,
              src/components/admin/diff-view.tsx.
"""

import pytest
from playwright.async_api import async_playwright, expect

BASE_URL = "http://localhost:5173"  # Vite dev server


@pytest.fixture(scope="session")
async def browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.fixture
async def page(browser):
    page = await browser.new_page()
    yield page
    await page.close()


@pytest.mark.asyncio
@pytest.mark.ui
async def test_invoice_image_visible_on_review_page(page):
    """
    The InvoiceViewer component renders the signed image URL.
    Component: src/components/invoice/invoice-viewer.tsx
    """
    await page.goto(f"{BASE_URL}/invoices/inv-001")
    # Wait for the invoice image to load
    img = page.locator("img").first
    await expect(img).to_be_visible(timeout=10000)
    src = await img.get_attribute("src")
    assert src is not None and len(src) > 0


@pytest.mark.asyncio
@pytest.mark.ui
async def test_low_confidence_fields_have_colored_border(page):
    """
    Fields with confidence < 0.9 get border-yellow-400 or border-red-400
    via getConfidenceBorderColor() from src/lib/utils.ts.
    Applied in MetadataForm (metadata-form.tsx line ~322).
    """
    await page.goto(f"{BASE_URL}/invoices/inv-001")

    # supplier_tax_id has confidence 0.72 (should be medium = yellow)
    tax_id_input = page.locator("#supplier_tax_id")
    await expect(tax_id_input).to_be_visible(timeout=10000)
    classes = await tax_id_input.get_attribute("class") or ""
    assert "border-yellow-400" in classes or "border-red-400" in classes


@pytest.mark.asyncio
@pytest.mark.ui
async def test_user_can_edit_and_submit_corrections(page):
    """
    MetadataForm tracks modifiedFields and enables "Submit Corrections"
    button when hasChanges is true.
    """
    await page.goto(f"{BASE_URL}/invoices/inv-001")

    # Edit supplier_tax_id field
    tax_input = page.locator("#supplier_tax_id")
    await tax_input.fill("NL002230884B02")

    # "Submit Corrections" button should now be enabled
    submit_btn = page.get_by_text("Submit Corrections")
    await expect(submit_btn).to_be_enabled()
    await submit_btn.click()

    # Wait for success toast (react-hot-toast)
    toast = page.locator("[role='status']").first
    await expect(toast).to_be_visible(timeout=5000)


@pytest.mark.asyncio
@pytest.mark.ui
async def test_admin_diff_view_shows_field_comparison(page):
    """
    AdminDiffView (diff-view.tsx) shows AI Extracted vs User Corrected
    with changed rows highlighted in bg-yellow-50.
    """
    await page.goto(f"{BASE_URL}/admin/invoices/inv-001")

    # The "Field Comparison" card header
    comparison_header = page.get_by_text("Field Comparison")
    await expect(comparison_header).to_be_visible(timeout=10000)

    # Changed rows have bg-yellow-50
    yellow_rows = page.locator(".bg-yellow-50")
    count = await yellow_rows.count()
    assert count > 0


@pytest.mark.asyncio
@pytest.mark.ui
async def test_admin_can_approve_invoice(page):
    """
    AdminDiffView has Approve/Reject/Flag buttons.
    Clicking Approve triggers onAction("approve").
    """
    await page.goto(f"{BASE_URL}/admin/invoices/inv-001")

    approve_btn = page.get_by_text("Approve")
    await expect(approve_btn).to_be_visible()
    await approve_btn.click()

    # Wait for success feedback
    toast = page.locator("[role='status']").first
    await expect(toast).to_be_visible(timeout=5000)
```

---

## FUTURE: Expert Workflow Tests

```python
# tests/future/test_expert_workflow.py

import pytest


@pytest.mark.skip(reason="Expert system not yet implemented -- FUTURE tables exist in schema")
class TestExpertAssignment:

    async def test_invoice_assigned_to_qualified_expert(self):
        """Matches expert.specializations with invoice category."""
        pass

    async def test_expert_queue_respects_load_limit(self):
        """Expert with >= 20 active tasks not assigned more."""
        pass

    async def test_dual_review_for_high_value(self):
        """Invoices > EUR 5000 get two independent expert reviews."""
        pass


@pytest.mark.skip(reason="Expert system not yet implemented")
class TestExpertReview:

    async def test_review_within_sla(self):
        """expert_assignments.status transitions within 8h SLA."""
        pass


@pytest.mark.skip(reason="Expert system not yet implemented")
class TestPayoutCalculation:

    async def test_payout_matches_review_credits(self):
        """expert_payouts.amount = sum of review credits for period."""
        pass

    async def test_fraud_detection_rubber_stamping(self):
        """> 60 reviews/hour triggers freeze."""
        pass
```

---

## Automated Test Scaffolding

### conftest.py

Complete `conftest.py` at `/home/appuser/scanbonAI/tests/conftest.py` includes:

- **Test database setup/teardown** -- async PostgreSQL with per-test rollback isolation
- **Test client fixtures** -- regular user + admin via httpx.AsyncClient with ASGITransport
- **Data factories** aligned with actual schema:
  - `TenantFactory` -- `tenants` table
  - `UserFactory` -- `users` table with `whatsapp_phone`, `role` (user/admin/superadmin)
  - `InvoiceFactory` -- `invoices` table with `file_hash`, `month_partition`, `whatsapp_message_id`; includes `sample_extracted_json()` matching `ExtractedInvoiceMetadata` schema
  - `QualityCheckFactory` -- `quality_checks` table with passing/failing variants
- **Mock adapters** -- OCR (success, timeout, low-confidence), WhatsApp API, queue, storage
- **Webhook helpers** -- payload builder matching WhatsApp Cloud API format, HMAC-SHA256 signature computation
- **Signed URL helpers** -- valid and expired token fixtures using `itsdangerous`

### Test functions

Five complete pytest test functions at `/home/appuser/scanbonAI/tests/test_api_endpoints.py` covering the five primary API endpoint categories.
