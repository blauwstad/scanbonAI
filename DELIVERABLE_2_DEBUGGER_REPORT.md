# DELIVERABLE 2: Debugger (Reliability Agent) Report

## Failure Modes Analysis

### 1. Webhook Signature Mismatch

| Aspect | Detail |
|---|---|
| **Trigger** | Meta rotates the WhatsApp Business API webhook signing key, or `WHATSAPP_WEBHOOK_SECRET` in `.env` is changed/corrupted during deployment. |
| **Impact** | All inbound webhooks rejected with 401. Zero new invoices processed. Users send photos, receive no acknowledgment (violates US-01 "acknowledge within 5 seconds"). |
| **Detection** | `scanbonai_webhooks_received_total{status="signature_invalid"}` spikes. Alert: rate > 10/min for 2 minutes. Zero `{status="accepted"}` in a 5-minute window. |
| **Mitigation** | Dual-key validation: accept both current and previous key during rotation. Log the signature header prefix (not the secret) for debugging. Health check includes signature key presence. |
| **Recovery** | Update `WHATSAPP_WEBHOOK_SECRET` in `.env`. Restart API container. Verify with test webhook. Meta retries unacknowledged webhooks for up to 7 days. |

### 2. Queue Stuck/Backlog

| Aspect | Detail |
|---|---|
| **Trigger** | Worker container crashes (OOM, unhandled exception), Redis goes down, or OCR service slowdown causes jobs to back up. |
| **Impact** | US-02 SLA violated: users do not receive confirmation within 60 seconds. Growing latency. `webhook_events` rows stuck in `status='received'` or `'processing'`. |
| **Detection** | `scanbonai_queue_depth` gauge > 100. `webhook_events` with `status='received'` and `created_at < NOW() - INTERVAL '5 min'`. Worker heartbeat missing. |
| **Mitigation** | Multiple worker replicas with auto-restart (`docker-compose` restart policy). Dead-letter queue for jobs failing 3 times (per US-12). Queue depth alerting. |
| **Recovery** | Restart worker: `docker compose restart worker`. Check Redis: `redis-cli PING`. Reprocess failed jobs from DLQ. |

### 3. OCR Service Latency/Timeout

| Aspect | Detail |
|---|---|
| **Trigger** | DeepSeek VL API degradation, rate limiting, or network issues between VPS and API. |
| **Impact** | Invoice processing stalls. `ocr_results.processing_time_ms` values spike. Queue builds up. |
| **Detection** | `scanbonai_ocr_duration_seconds` p99 > 30s. Circuit breaker opens. `ocr_results` creation rate drops to zero. |
| **Mitigation** | 30-second timeout. 3 retries with exponential backoff (1s, 4s, 16s). Circuit breaker (open after 5 consecutive failures, half-open after 60s). |
| **Recovery** | Check DeepSeek API status. Reset circuit breaker if needed. Once restored, queued jobs auto-retry. |

### 4. Broken Signed Links

| Aspect | Detail |
|---|---|
| **Trigger** | Server clock skew, `SIGNING_KEY` rotation without grace period, or `signed_links.expires_at` miscalculated. |
| **Impact** | Users tap WhatsApp review links (US-02) and get 403. Cannot review invoices. Trust eroded. |
| **Detection** | `scanbonai_signed_links_expired_total` spike. Error rate on `/api/v1/signed/*` > 20%. `signed_links.accessed_at` remains NULL for recent links. |
| **Mitigation** | NTP sync (chrony). Dual-key validation during rotation. 72-hour expiry per US-02/US-11. User can request new link via WhatsApp "STATUS" command (US-10). |
| **Recovery** | Fix clock or revert key. Regenerate links for affected invoices. Resend via WhatsApp. |

### 5. Database Migration Failure

| Aspect | Detail |
|---|---|
| **Trigger** | Alembic migration bug: wrong column type, missing default on NOT NULL, constraint violation on existing data. |
| **Impact** | Application fails to start. `invoices`, `extracted_data`, or other tables in inconsistent state. |
| **Detection** | Deployment health check (`/health`) fails. All DB-dependent routes return 500. |
| **Mitigation** | Test migrations on staging DB clone. PostgreSQL transactional DDL. `pg_dump` backup before every migration. Small, reversible migrations. |
| **Recovery** | `alembic downgrade -1`. If that fails, restore from `/data/scanbonai/backups/db/`. Fix migration, test, redeploy. |

### 6. File Storage Full

| Aspect | Detail |
|---|---|
| **Trigger** | VPS disk fills from accumulated images at `/data/scanbonai/invoices/{tenant_id}/{user_id}/YYYY-MM/`, logs, or temp files. |
| **Impact** | New uploads fail. Worker crashes writing to `/data/`. PostgreSQL WAL may be affected if same volume. |
| **Detection** | `node_filesystem_avail_bytes` < 1GB. Upload failures in logs. `OSError: No space left on device`. |
| **Mitigation** | Separate `/data/scanbonai/` on its own partition (per DELIVERABLES.md). Log rotation. Disk alert at 80%. |
| **Recovery** | Clean temp files. Compress/archive old invoices. Expand volume. |

### 7. WhatsApp API Rate Limit

| Aspect | Detail |
|---|---|
| **Trigger** | Too many outbound messages (confirmations, review links, status replies per US-10). Meta Business API tier limits. |
| **Impact** | Outbound messages delayed or dropped. Users do not receive review links or confirmations. |
| **Detection** | WhatsApp API returns 429. `scanbonai_whatsapp_send_errors_total{type="rate_limit"}` increasing. |
| **Mitigation** | Outbound queue with token bucket rate limiter. Priority: review links > confirmations > status replies. |
| **Recovery** | Wait for rate limit window to reset. Drain queue gradually. Upgrade WhatsApp tier if recurrent. |

### 8. Duplicate Processing

| Aspect | Detail |
|---|---|
| **Trigger** | Queue delivers same job twice (worker crash before ack, Redis failover). WhatsApp re-delivers same webhook. |
| **Impact** | Duplicate rows in `invoices`. User sees same invoice twice in list. Metrics double-counted. |
| **Detection** | UNIQUE constraint violation on `webhook_events.whatsapp_message_id` or `invoices(tenant_id, file_hash)`. |
| **Mitigation** | `webhook_events` UNIQUE on `whatsapp_message_id`. `invoices` UNIQUE on `(tenant_id, file_hash)`. Idempotent `INSERT ... ON CONFLICT DO NOTHING`. |
| **Recovery** | Query duplicates. Merge or soft-delete the less complete record. |

### 9. Memory Leak in Worker

| Aspect | Detail |
|---|---|
| **Trigger** | Image processing accumulates unreleased buffers. Large images loaded fully into memory. Long-running worker without restart. |
| **Impact** | Worker RSS grows until OOM kill. Processing halts. Crash-loop if same image causes leak. |
| **Detection** | `process_resident_memory_bytes{job="worker"}` steadily increasing. OOM entries in `dmesg`. Container restart count increasing. |
| **Mitigation** | Memory limit in docker-compose (`mem_limit: 512m`). Worker recycles after N jobs. `gc.collect()` after each job. Image size validation before loading. |
| **Recovery** | Restart worker. Profile with `tracemalloc`. Fix leak. Set up periodic recycling. |

### 10. Tenant Data Leak

| Aspect | Detail |
|---|---|
| **Trigger** | Bug in query omitting `tenant_id` filter. Signed URL generated for wrong tenant. Admin endpoint exposed to non-admin. |
| **Impact** | **CRITICAL**: User sees another tenant's invoices or financial data. Regulatory violation (GDPR, Dutch tax law). |
| **Detection** | Automated tenant isolation tests. `audit_log` entries showing cross-tenant access. Penetration testing. |
| **Mitigation** | Row-Level Security (RLS) in PostgreSQL. Middleware auto-injecting `tenant_id`. All repository methods require `tenant_id` parameter. `idx_invoices_tenant_id` index. |
| **Recovery** | Immediately disable affected endpoint. Audit `audit_log` for scope. Notify affected tenants. Fix bug. Full security review. |

---

## Debugging Strategy

### Correlation IDs

```python
# app/middleware/correlation.py
"""
Generates and propagates correlation IDs through the full request lifecycle.
API -> webhook_events -> Redis job -> Worker -> OCR call -> DB writes -> audit_log.
"""

import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")


def get_correlation_id() -> str:
    return correlation_id_var.get()


class CorrelationMiddleware(BaseHTTPMiddleware):
    """
    Extracts or generates X-Request-ID, propagates through context.
    Traefik (reverse proxy) can inject this header upstream.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        correlation_id_var.set(request_id)

        if hasattr(request.state, "tenant_id"):
            tenant_id_var.set(request.state.tenant_id)
        if hasattr(request.state, "user_id"):
            user_id_var.set(request.state.user_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def build_job_context() -> dict:
    """
    Serialize current context into a Redis job payload
    so the worker inherits the correlation.
    """
    return {
        "correlation_id": get_correlation_id(),
        "tenant_id": tenant_id_var.get(),
        "user_id": user_id_var.get(),
    }


def restore_job_context(context: dict) -> None:
    """Restore correlation context inside a worker from job payload."""
    correlation_id_var.set(context.get("correlation_id", str(uuid.uuid4())))
    tenant_id_var.set(context.get("tenant_id", ""))
    user_id_var.set(context.get("user_id", ""))
```

### Structured Logging

```python
# app/utils/logging.py
"""
Structured JSON logging with PII redaction.
Referenced in the VPS folder structure as app/utils/logging.py.
Every log entry includes correlation_id, tenant_id, and contextual metadata.
"""

import logging
import json
import traceback
from datetime import datetime, timezone

from app.middleware.correlation import get_correlation_id, tenant_id_var, user_id_var


class StructuredJSONFormatter(logging.Formatter):
    """Single-line JSON log entries for aggregation (Loki, ELK, etc.)."""

    # Fields that may contain PII and should be redacted
    PII_FIELDS = {"whatsapp_phone", "phone_number", "display_name", "email"}

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
            "tenant_id": tenant_id_var.get(""),
            "user_id": user_id_var.get(""),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add optional fields
        for field in (
            "invoice_id", "whatsapp_message_id", "request_method",
            "request_path", "response_status", "duration_ms",
            "ocr_duration_ms", "quality_score", "job_id",
        ):
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value

        # PII redaction
        for field in self.PII_FIELDS:
            if field in log_entry:
                val = str(log_entry[field])
                if len(val) > 4:
                    log_entry[field] = val[:2] + "*" * (len(val) - 4) + val[-2:]

        # Exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "stacktrace": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_entry, default=str, ensure_ascii=False)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        record.tenant_id = tenant_id_var.get("")
        record.user_id = user_id_var.get("")
        return True


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "app.utils.logging.StructuredJSONFormatter"},
        "simple": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "filters": {
        "context": {"()": "app.utils.logging.RequestContextFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["context"],
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "/data/scanbonai/logs/api/app.log",
            "maxBytes": 52428800,  # 50 MB
            "backupCount": 5,
            "formatter": "json",
            "filters": ["context"],
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "/data/scanbonai/logs/api/error.log",
            "maxBytes": 52428800,
            "backupCount": 10,
            "level": "ERROR",
            "formatter": "json",
            "filters": ["context"],
        },
    },
    "loggers": {
        "app": {
            "level": "INFO",
            "handlers": ["console", "file", "error_file"],
            "propagate": False,
        },
        "app.tasks": {
            "level": "INFO",
            "handlers": ["console", "file", "error_file"],
            "propagate": False,
        },
        "app.services.ocr": {
            "level": "DEBUG",
            "handlers": ["console", "file"],
            "propagate": False,
        },
        "uvicorn": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
    },
    "root": {
        "level": "WARNING",
        "handlers": ["console", "file"],
    },
}
```

### Tracing (OpenTelemetry)

```python
# app/core/tracing.py
"""
OpenTelemetry distributed tracing for ScanbonAI.
Spans for each pipeline stage: webhook -> queue -> quality -> OCR -> storage.
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from app.config import settings


def setup_tracing(app=None, engine=None):
    resource = Resource.create({
        "service.name": "scanbonai-api",
        "service.version": settings.APP_VERSION,
        "deployment.environment": settings.ENVIRONMENT,
    })

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.OTLP_ENDPOINT,
        insecure=settings.ENVIRONMENT != "production",
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    if app:
        FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    if engine:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)

    return provider


tracer = trace.get_tracer("scanbonai")


# Pipeline stage decorators

def trace_webhook_receive(func):
    async def wrapper(*args, **kwargs):
        with tracer.start_as_current_span("webhook.receive",
            attributes={"webhook.source": "whatsapp"}) as span:
            result = await func(*args, **kwargs)
            span.set_attribute("webhook.message_id", result.get("message_id", ""))
            return result
    return wrapper


def trace_quality_check(func):
    async def wrapper(*args, **kwargs):
        with tracer.start_as_current_span("quality_check") as span:
            result = await func(*args, **kwargs)
            if hasattr(result, "overall_pass"):
                span.set_attribute("quality.pass", result.overall_pass)
            return result
    return wrapper


def trace_ocr_call(func):
    async def wrapper(*args, **kwargs):
        with tracer.start_as_current_span("ocr.extract",
            attributes={"ocr.provider": "deepseek-vl"}) as span:
            result = await func(*args, **kwargs)
            span.set_attribute("ocr.duration_ms", result.get("processing_time_ms", 0))
            return result
    return wrapper


def trace_db_storage(func):
    async def wrapper(*args, **kwargs):
        with tracer.start_as_current_span("db.store_invoice") as span:
            result = await func(*args, **kwargs)
            if hasattr(result, "id"):
                span.set_attribute("db.invoice_id", str(result.id))
            return result
    return wrapper
```

---

## Idempotency Design

### Webhook Deduplication

The `webhook_events` table has `whatsapp_message_id VARCHAR(128) NOT NULL UNIQUE`. Additionally, `invoices` has `UNIQUE (tenant_id, file_hash)` for file-level dedup within the same tenant (per US-01: same file hash within 24h returns existing receipt).

```python
# app/services/webhook_handler.py

import logging
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_event import WebhookEvent
from app.models.invoice import Invoice

logger = logging.getLogger("app.services.webhook")


async def handle_incoming_message(
    msg_data: dict,
    session: AsyncSession,
) -> dict:
    """
    Idempotent webhook processing.
    1. INSERT INTO webhook_events ON CONFLICT DO NOTHING (whatsapp_message_id).
    2. If no rows affected -> already processed.
    3. Check invoices(tenant_id, file_hash) for file-level dedup.
    """
    message_id = msg_data["whatsapp_message_id"]

    # Webhook-level dedup
    stmt = (
        pg_insert(WebhookEvent)
        .values(
            whatsapp_message_id=message_id,
            payload=msg_data,
            status="received",
        )
        .on_conflict_do_nothing(index_elements=["whatsapp_message_id"])
        .returning(WebhookEvent.id)
    )
    result = await session.execute(stmt)
    row = result.fetchone()

    if row is None:
        existing = await session.execute(
            select(WebhookEvent.id).where(
                WebhookEvent.whatsapp_message_id == message_id
            )
        )
        existing_id = existing.scalar_one()
        logger.info(
            f"Duplicate webhook {message_id}, existing event {existing_id}",
            extra={"whatsapp_message_id": message_id},
        )
        return {"created": False, "existing_event_id": str(existing_id)}

    # File-level dedup (same tenant + same file hash within 24h)
    file_hash = msg_data.get("file_hash")
    if file_hash:
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        existing_invoice = await session.execute(
            select(Invoice.id).where(
                Invoice.tenant_id == msg_data["tenant_id"],
                Invoice.file_hash == file_hash,
                Invoice.created_at > cutoff,
            )
        )
        dup = existing_invoice.scalar_one_or_none()
        if dup:
            logger.info(
                f"Duplicate file hash {file_hash[:16]}... for tenant {msg_data['tenant_id']}",
                extra={"invoice_id": str(dup)},
            )
            return {"created": False, "existing_invoice_id": str(dup)}

    return {"created": True, "event_id": str(row[0])}
```

### Queue Job Idempotency

```python
# app/tasks/job_idempotency.py

import hashlib
import logging
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.tasks.idempotency")


def derive_job_id(invoice_id: str, operation: str) -> str:
    """Deterministic job ID from invoice_id + operation."""
    return hashlib.sha256(f"{invoice_id}:{operation}".encode()).hexdigest()[:32]


async def should_process_job(job_id: str, session: AsyncSession) -> bool:
    """Check webhook_events status; return False if already completed."""
    result = await session.execute(
        text("SELECT status FROM webhook_events WHERE id = :jid"),
        {"jid": job_id},
    )
    row = result.scalar_one_or_none()

    if row == "processed":
        logger.info(f"Job {job_id} already processed, skipping")
        return False
    if row == "processing":
        logger.warning(f"Job {job_id} already in progress")
        return False

    # Mark as processing
    await session.execute(
        text("UPDATE webhook_events SET status = 'processing' WHERE id = :jid"),
        {"jid": job_id},
    )
    await session.commit()
    return True


async def mark_job_completed(job_id: str, session: AsyncSession) -> None:
    await session.execute(
        text("UPDATE webhook_events SET status = 'processed', processed_at = NOW() WHERE id = :jid"),
        {"jid": job_id},
    )
    await session.commit()


async def mark_job_failed(job_id: str, session: AsyncSession) -> None:
    await session.execute(
        text("""
            UPDATE webhook_events
            SET status = 'failed', retry_count = retry_count + 1
            WHERE id = :jid
        """),
        {"jid": job_id},
    )
    await session.commit()
```

### Corrections: Optimistic Locking

The `invoices` table has `updated_at` with a trigger (`trigger_set_updated_at`). We add optimistic locking via a version check on `updated_at`.

```python
# app/services/invoices.py (optimistic locking excerpt)

from sqlalchemy import update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.invoice import Invoice


class OptimisticLockError(Exception):
    pass


async def apply_correction(
    invoice_id: str,
    tenant_id: str,
    corrected_data: dict,
    field_corrections: list,
    expected_updated_at: str,
    session: AsyncSession,
) -> dict:
    from app.services.diff import compute_invoice_diff
    from app.models.user_correction import UserCorrection
    from datetime import datetime

    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"Invoice {invoice_id} not found")
    if invoice.tenant_id != tenant_id:
        raise PermissionError("Cross-tenant access denied")

    # Optimistic lock: check updated_at has not changed
    if str(invoice.updated_at) != expected_updated_at:
        raise OptimisticLockError(
            f"Invoice {invoice_id} was modified. Refresh and retry."
        )

    # Compute diff
    # ... create UserCorrection row with diff_json ...

    # Update invoice status
    invoice.status = "reviewed"
    await session.commit()

    return {"id": invoice_id, "status": "reviewed"}
```

---

## Retry Logic

```python
# app/core/retry.py
"""
Retry with exponential backoff and circuit breaker for OCR and WhatsApp calls.
Per US-12: "Failed jobs are retried up to 3 times with exponential backoff."
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Type

logger = logging.getLogger("app.core.retry")


@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0
    backoff_factor: float = 4.0   # delays: 1s, 4s, 16s
    max_delay: float = 60.0
    retryable_exceptions: tuple[Type[Exception], ...] = (
        asyncio.TimeoutError, ConnectionError, OSError,
    )


async def retry_with_backoff(func: Callable, config: RetryConfig = RetryConfig(), **kwargs) -> Any:
    last_exc = None
    for attempt in range(config.max_retries + 1):
        try:
            return await func(**kwargs)
        except config.retryable_exceptions as exc:
            last_exc = exc
            if attempt == config.max_retries:
                logger.error(f"All {config.max_retries} retries exhausted for {func.__name__}", exc_info=True)
                raise
            delay = min(config.base_delay * (config.backoff_factor ** attempt), config.max_delay)
            logger.warning(f"Attempt {attempt+1}/{config.max_retries} failed: {exc}. Retry in {delay:.1f}s")
            await asyncio.sleep(delay)
    raise last_exc


# Pre-configured
OCR_RETRY = RetryConfig(max_retries=3, base_delay=1.0, backoff_factor=4.0)     # 1s, 4s, 16s
WHATSAPP_RETRY = RetryConfig(max_retries=3, base_delay=2.0, backoff_factor=2.0) # 2s, 4s, 8s


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str = "default"
    failure_threshold: int = 5
    recovery_timeout: float = 60.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        async with self._lock:
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. Retry after {self.recovery_timeout}s."
                )

        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                self._failure_count = 0
                self._state = CircuitState.CLOSED
            return result
        except Exception:
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.critical(f"Circuit '{self.name}' OPENED after {self._failure_count} failures")
            raise


class CircuitBreakerOpenError(Exception):
    pass


# Pre-configured breakers
ocr_circuit = CircuitBreaker(name="ocr_deepseek", failure_threshold=5, recovery_timeout=60.0)
whatsapp_circuit = CircuitBreaker(name="whatsapp_api", failure_threshold=5, recovery_timeout=30.0)
```

### Worker Retry + Dead Letter Queue

```python
# app/tasks/ocr_tasks.py (retry + DLQ excerpt)

import logging
from app.core.retry import retry_with_backoff, OCR_RETRY, ocr_circuit
from app.tasks.job_idempotency import should_process_job, mark_job_completed, mark_job_failed

logger = logging.getLogger("app.tasks.ocr")
MAX_ATTEMPTS = 3


async def process_invoice_job(job_data: dict, session, attempt: int = 1) -> dict:
    """
    1. Check idempotency (webhook_events.status)
    2. Download image, run quality_check
    3. If quality passes: OCR with retry + circuit breaker
    4. Store results in ocr_results + extracted_data
    5. Generate signed_link, send WhatsApp confirmation
    6. On exhaustion: move to DLQ, notify user
    """
    event_id = job_data.get("event_id")

    if event_id and not await should_process_job(event_id, session):
        return {"status": "already_processed"}

    try:
        # ... quality check ...
        # ... download image ...

        # OCR with retry + circuit breaker
        async def _ocr_call():
            return await ocr_circuit.call(ocr_adapter.extract, image_data=image_bytes)

        extraction = await retry_with_backoff(_ocr_call, config=OCR_RETRY)

        # ... store in ocr_results, extracted_data ...
        # ... create signed_link row ...
        # ... send WhatsApp confirmation ...

        if event_id:
            await mark_job_completed(event_id, session)
        return {"status": "extracted", "invoice_id": invoice_id}

    except Exception as exc:
        if attempt >= MAX_ATTEMPTS:
            if event_id:
                await mark_job_failed(event_id, session)
            # Dead letter queue
            await dead_letter_queue.enqueue({**job_data, "error": str(exc), "attempts": attempt})
            logger.error(f"Job {event_id} failed after {attempt} attempts, moved to DLQ", exc_info=True)
            # Notify user
            await notify_user_failure(job_data["phone_number"], job_data["tenant_id"])
            return {"status": "failed"}
        raise  # Let queue framework retry
```

---

## Runbook

### 1. Webhook Signature Mismatch

**Symptoms:** `scanbonai_webhooks_received_total{status="signature_invalid"}` spikes. No new invoices. Users get no WhatsApp acknowledgment.

**Diagnosis:**

```bash
# Check recent webhook errors
docker compose logs api --since 10m | grep -i "signature"

# Verify secret key is set (check hash, not value)
docker compose exec api python3 -c "
import os, hashlib
s = os.environ.get('WHATSAPP_WEBHOOK_SECRET', '')
print(f'Set: {bool(s)}, Hash prefix: {hashlib.sha256(s.encode()).hexdigest()[:16]}')
"

# Check webhook_events table
docker compose exec db psql -U scanbonai -c "
  SELECT status, COUNT(*) FROM webhook_events
  WHERE created_at > NOW() - INTERVAL '1 hour'
  GROUP BY status;
"
```

**Fix:**

1. Get current signing key from Meta Business Suite > App Dashboard > Webhooks.
2. Update in `.env`: `WHATSAPP_WEBHOOK_SECRET=new_key_here`.
3. `docker compose up -d api` to restart.
4. Send test image via WhatsApp, verify processing.
5. Meta retries unacknowledged webhooks automatically.

**Prevention:** Dual-key validation. Monitor `signature_invalid` counter. Subscribe to Meta developer notifications.

---

### 2. Queue Stuck/Growing

**Symptoms:** `scanbonai_queue_depth` > 100. Users waiting. `webhook_events.status='received'` growing.

**Diagnosis:**

```bash
# Redis queue depth
docker compose exec redis redis-cli LLEN scanbonai:ocr_jobs

# Worker status
docker compose ps worker
docker compose logs worker --since 30m --tail 100

# Stuck webhook events
docker compose exec db psql -U scanbonai -c "
  SELECT COUNT(*), status FROM webhook_events
  WHERE created_at > NOW() - INTERVAL '2 hours'
  GROUP BY status;
"

# Dead letter queue
docker compose exec redis redis-cli LLEN scanbonai:ocr_jobs:dlq
```

**Fix:**

1. Restart worker: `docker compose restart worker`.
2. If crash-looping, check logs for root cause.
3. If OCR-caused, see Runbook #3.
4. Scale temporarily: `docker compose up -d --scale worker=3`.
5. Reprocess DLQ once root cause fixed.

**Prevention:** `restart: unless-stopped` in docker-compose. Queue depth alerting. Worker health endpoint.

---

### 3. OCR Latency Spike

**Symptoms:** `scanbonai_ocr_duration_seconds` p99 > 30s. Circuit breaker open.

**Diagnosis:**

```bash
# Check OCR timing from logs
docker compose logs worker --since 15m | grep "processing_time_ms"

# Check circuit breaker state
curl -s http://localhost:8000/health | python3 -m json.tool

# Network test to DeepSeek
curl -w "connect: %{time_connect}s\ntotal: %{time_total}s\n" -o /dev/null -s https://api.deepseek.com/
```

**Fix:**

1. If DeepSeek is down, wait or switch to backup OCR provider.
2. Reset circuit breaker if needed: `curl -X POST http://localhost:8000/internal/circuit/ocr_deepseek/reset`.
3. Queued jobs auto-retry when OCR recovers.

**Prevention:** Circuit breaker (60s recovery). Backup OCR provider. Alerting on p99 latency.

---

### 4. Broken Signed Links

**Symptoms:** `scanbonai_signed_links_expired_total` spike. Users report 403 on review links.

**Diagnosis:**

```bash
# Check clock
timedatectl status
chronyc tracking

# Check signing key (hash only)
docker compose exec api python3 -c "
import os, hashlib
k = os.environ.get('SIGNING_KEY', '')
print(f'Set: {bool(k)}, Hash: {hashlib.sha256(k.encode()).hexdigest()[:16]}')
"

# Check recent signed_links
docker compose exec db psql -U scanbonai -c "
  SELECT COUNT(*), CASE WHEN expires_at < NOW() THEN 'expired' ELSE 'valid' END as state
  FROM signed_links WHERE created_at > NOW() - INTERVAL '24 hours'
  GROUP BY state;
"
```

**Fix:**

1. Fix clock: `sudo chronyc makestep`.
2. If key rotated: revert or implement dual-key.
3. Regenerate links for affected invoices.
4. Resend via WhatsApp.

**Prevention:** NTP sync. 72-hour expiry (US-11). "STATUS" command for new links (US-10).

---

### 5. DB Migration Rollback

**Symptoms:** App returns 500 after deployment. `/health` fails.

**Diagnosis:**

```bash
# Check migration state
docker compose exec api alembic current
docker compose exec api alembic history -r -5:

# Check DB errors
docker compose logs api --since 5m | grep -i "error\|migration"
```

**Fix:**

1. Rollback: `docker compose exec api alembic downgrade -1`.
2. If that fails: restore from backup `gunzip -c /data/scanbonai/backups/db/latest.sql.gz | docker compose exec -T db psql -U scanbonai`.
3. Fix migration, test on staging, redeploy.

**Prevention:** `pg_dump` before migration (via `scripts/backup.sh`). Test on staging clone. Small reversible migrations.

---

### 6. Disk Space Alert

**Symptoms:** `node_filesystem_avail_bytes` < 1GB. Upload failures.

**Diagnosis:**

```bash
df -h /data/scanbonai/
du -sh /data/scanbonai/invoices/*/  | sort -rh | head -10
du -sh /data/scanbonai/logs/*/ | sort -rh | head -10
du -sh /data/scanbonai/postgres/data/
```

**Fix:**

1. Clean temp files.
2. Compress old logs.
3. Archive invoices older than 90 days to object storage.
4. If PostgreSQL WAL large: check replication slots `SELECT * FROM pg_replication_slots;`.
5. Expand volume if needed.

**Prevention:** Separate `/data/scanbonai/` partition. Log rotation. 80% disk alerting. Automated archival.

---

### 7. WhatsApp Rate Limit Hit

**Symptoms:** `scanbonai_whatsapp_send_errors_total{type="rate_limit"}` increasing.

**Diagnosis:**

```bash
docker compose logs api --since 10m | grep -i "rate_limit\|429"
docker compose exec redis redis-cli LLEN scanbonai:whatsapp_outbound
```

**Fix:**

1. Outbound queue drains naturally as rate limit resets.
2. Prioritize review links over status replies.
3. Upgrade WhatsApp Business tier for higher limits.

**Prevention:** Token bucket rate limiter. Priority queue. Batch operations use semaphore.

---

### 8. Duplicate Invoice Records Found

**Symptoms:** Admin sees same invoice twice. Metrics double-counted.

**Diagnosis:**

```bash
docker compose exec db psql -U scanbonai -c "
  SELECT tenant_id, file_hash, COUNT(*), array_agg(id) as ids
  FROM invoices GROUP BY tenant_id, file_hash HAVING COUNT(*) > 1
  ORDER BY COUNT(*) DESC LIMIT 20;
"
```

**Fix:**

1. Identify more complete record.
2. Soft-delete duplicate: `UPDATE invoices SET status = 'duplicate_removed' WHERE id = 'DUP_ID';`.
3. If UNIQUE constraint missing, add it.

**Prevention:** UNIQUE on `(tenant_id, file_hash)` (already in schema). Idempotent webhook handler. Job-level idempotency.

---

### 9. Worker OOM/Crash Loop

**Symptoms:** Worker restarting. `docker compose ps` shows restart count increasing.

**Diagnosis:**

```bash
dmesg | grep -i "oom\|killed" | tail -20
docker compose ps worker
docker stats scanbonai-worker-1 --no-stream
docker compose logs worker --since 5m | tail -50
```

**Fix:**

1. Restart with profiling: add `PYTHONTRACEMALLOC=1` to worker env.
2. Check for large images loaded into memory.
3. Set `mem_limit: 512m` in docker-compose.
4. Add `--max-jobs 500` to recycle worker.

**Prevention:** Memory limit in docker-compose. Worker recycling. Image size validation. `gc.collect()` per job.

---

### 10. Suspected Data Leak

**Symptoms:** User reports seeing another tenant's data. Audit log anomaly.

**Diagnosis:**

```bash
# IMMEDIATE: disable affected endpoint

# Check audit_log for cross-tenant access
docker compose exec db psql -U scanbonai -c "
  SELECT al.created_at, al.user_id, al.tenant_id, al.action,
         al.entity_type, al.entity_id, i.tenant_id as invoice_tenant
  FROM audit_log al
  LEFT JOIN invoices i ON al.entity_id = i.id AND al.entity_type = 'invoice'
  WHERE al.tenant_id != i.tenant_id
  AND al.created_at > NOW() - INTERVAL '7 days'
  ORDER BY al.created_at DESC LIMIT 50;
"

# Check for queries missing tenant_id filter
grep -rn "SELECT.*FROM invoices" app/ | grep -v "tenant_id"
```

**Fix:**

1. Disable affected endpoint/feature immediately.
2. Determine scope from `audit_log`.
3. Fix the bug (add tenant_id filter).
4. Deploy hotfix.
5. Audit all similar endpoints.
6. Notify affected tenants per breach policy.

**Prevention:** RLS in PostgreSQL. Mandatory `tenant_id` on all repo methods. Automated tenant isolation tests. Regular penetration testing.

---

## FUTURE: Expert System Reliability

```python
# app/monitoring/expert_checks.py (future)

async def check_payout_reconciliation():
    """Compare sum of review credits with expert_payouts.amount for period."""
    pass

async def detect_expert_abuse():
    """
    Flags:
    - > 60 reviews/hour (rubber-stamping per spec section 4.3)
    - > 95% approval with no corrections
    - Review time < 10 seconds consistently
    - All reviews for single tenant (collusion)
    """
    pass

async def detect_label_conflicts():
    """
    expert_disputes created when dual-review experts disagree
    (per spec section 4.4).
    """
    pass

async def check_model_drift():
    """
    Compare OCR outputs against expert-corrected ground truth
    (training_datasets table). Alert if accuracy drops.
    """
    pass
```

---

## Instrumentation and Metrics Plan

### Prometheus Metrics Definition

```python
# app/core/metrics.py
"""
Prometheus metrics for ScanbonAI.
Exposed via /metrics endpoint (per US-12).
"""

from prometheus_client import Counter, Histogram, Gauge, Info, CollectorRegistry, generate_latest

REGISTRY = CollectorRegistry()

# --- Counters ---

WEBHOOKS_RECEIVED = Counter(
    "scanbonai_webhooks_received_total",
    "Total WhatsApp webhooks received",
    labelnames=["status"],  # accepted, signature_invalid, malformed, rate_limited
    registry=REGISTRY,
)

INVOICES_PROCESSED = Counter(
    "scanbonai_invoices_processed_total",
    "Total invoices processed through pipeline",
    labelnames=["status"],  # extracted, quality_failed, ocr_failed, not_invoice
    registry=REGISTRY,
)

CORRECTIONS_TOTAL = Counter(
    "scanbonai_corrections_total",
    "Total user corrections submitted (user_corrections table)",
    labelnames=["tenant_id"],
    registry=REGISTRY,
)

SIGNED_LINKS_EXPIRED = Counter(
    "scanbonai_signed_links_expired_total",
    "Total expired signed link access attempts",
    registry=REGISTRY,
)

OCR_ERRORS = Counter(
    "scanbonai_ocr_errors_total",
    "Total OCR extraction errors",
    labelnames=["type"],  # timeout, api_error, circuit_open
    registry=REGISTRY,
)

WHATSAPP_SEND_ERRORS = Counter(
    "scanbonai_whatsapp_send_errors_total",
    "Total WhatsApp outbound message errors",
    labelnames=["type"],  # rate_limit, auth_error, network_error
    registry=REGISTRY,
)

DUPLICATES_DETECTED = Counter(
    "scanbonai_duplicates_detected_total",
    "Duplicate webhook/file detections (webhook_events + invoices dedup)",
    registry=REGISTRY,
)

# --- Histograms ---

OCR_DURATION = Histogram(
    "scanbonai_ocr_duration_seconds",
    "OCR extraction duration (maps to ocr_results.processing_time_ms)",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0],
    registry=REGISTRY,
)

WEBHOOK_PROCESSING_DURATION = Histogram(
    "scanbonai_webhook_processing_duration_seconds",
    "Time from webhook receipt to job enqueue",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
    registry=REGISTRY,
)

PIPELINE_TOTAL_DURATION = Histogram(
    "scanbonai_pipeline_total_duration_seconds",
    "Total time from webhook to WhatsApp confirmation (US-02: < 60s for < 5MB)",
    buckets=[5, 10, 30, 60, 120, 300, 600],
    registry=REGISTRY,
)

# --- Gauges ---

QUALITY_CHECK_PASS_RATE = Gauge(
    "scanbonai_quality_check_pass_rate",
    "Rolling pass rate of quality_checks.overall_pass",
    registry=REGISTRY,
)

EXTRACTION_CONFIDENCE_AVG = Gauge(
    "scanbonai_extraction_confidence_avg",
    "Average extraction confidence per field (from extracted_data.confidence_scores)",
    labelnames=["field"],
    registry=REGISTRY,
)

QUEUE_DEPTH = Gauge(
    "scanbonai_queue_depth",
    "Current OCR job queue depth in Redis",
    registry=REGISTRY,
)

ACTIVE_WORKERS = Gauge(
    "scanbonai_active_workers",
    "Number of active worker containers",
    registry=REGISTRY,
)

CIRCUIT_BREAKER_STATE = Gauge(
    "scanbonai_circuit_breaker_state",
    "0=closed, 1=half_open, 2=open",
    labelnames=["service"],
    registry=REGISTRY,
)

APP_INFO = Info("scanbonai", "Application metadata", registry=REGISTRY)
```

### Alert Rules (Prometheus/Alertmanager)

```yaml
# alerting/scanbonai_alerts.yml

groups:
  - name: scanbonai_critical
    rules:

      - alert: QueueDepthHigh
        expr: scanbonai_queue_depth > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "OCR queue depth {{ $value }} jobs (> 100 for 5m)"
          runbook: "Runbook #2: Queue Stuck/Growing"

      - alert: QueueDepthCritical
        expr: scanbonai_queue_depth > 500
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "OCR queue critically backed up ({{ $value }} jobs)"

      - alert: OCRLatencyHigh
        expr: histogram_quantile(0.99, rate(scanbonai_ocr_duration_seconds_bucket[5m])) > 30
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "OCR p99 latency {{ $value }}s (> 30s)"
          runbook: "Runbook #3: OCR Latency Spike"

      - alert: OCRCircuitBreakerOpen
        expr: scanbonai_circuit_breaker_state{service="ocr_deepseek"} == 2
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "OCR circuit breaker OPEN"

      - alert: WebhookErrorRateHigh
        expr: >
          rate(scanbonai_webhooks_received_total{status=~"signature_invalid|malformed"}[5m])
          / rate(scanbonai_webhooks_received_total[5m]) > 0.05
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "Webhook error rate > 5%"
          runbook: "Runbook #1: Webhook Signature Mismatch"

      - alert: AllWebhooksRejected
        expr: >
          rate(scanbonai_webhooks_received_total{status="accepted"}[5m]) == 0
          AND rate(scanbonai_webhooks_received_total[5m]) > 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "All webhooks rejected! Likely key mismatch."

      - alert: SignedLinkExpirySpike
        expr: rate(scanbonai_signed_links_expired_total[5m]) > 10
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "Spike in expired signed link access"
          runbook: "Runbook #4: Broken Signed Links"

      - alert: WorkerDown
        expr: scanbonai_active_workers == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "No active workers! Processing halted."

      - alert: DiskSpaceLow
        expr: node_filesystem_avail_bytes{mountpoint="/data"} < 1073741824
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Disk space below 1GB on /data"
          runbook: "Runbook #6: Disk Space Alert"

      - alert: DiskSpaceCritical
        expr: node_filesystem_avail_bytes{mountpoint="/data"} < 268435456
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Disk space below 256MB! Uploads will fail."

      - alert: WhatsAppRateLimited
        expr: rate(scanbonai_whatsapp_send_errors_total{type="rate_limit"}[5m]) > 1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "WhatsApp API rate limiting detected"
          runbook: "Runbook #7: WhatsApp Rate Limit Hit"

      - alert: PipelineSLABreach
        expr: histogram_quantile(0.95, rate(scanbonai_pipeline_total_duration_seconds_bucket[5m])) > 60
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Pipeline p95 > 60s (US-02 SLA breach)"
```

### Metrics Endpoint

```python
# app/api/internal/metrics.py
"""
Internal metrics endpoint for Prometheus scraping.
Not publicly exposed -- accessed only from monitoring infrastructure.
Mapped in architecture: /metrics endpoint per US-12.
"""

from fastapi import APIRouter, Response
from prometheus_client import generate_latest
from app.core.metrics import REGISTRY
from app.core.retry import ocr_circuit, whatsapp_circuit

router = APIRouter(tags=["internal"])


@router.get("/metrics")
async def prometheus_metrics():
    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/health")
async def health_check():
    """
    Per US-12: /health returns service status, DB, Redis, storage.
    """
    # TODO: actual connectivity checks
    return {
        "status": "healthy",
        "components": {
            "database": "ok",
            "redis": "ok",
            "storage": "ok",
            "ocr_circuit": ocr_circuit.state.value,
            "whatsapp_circuit": whatsapp_circuit.state.value,
        },
    }
```
