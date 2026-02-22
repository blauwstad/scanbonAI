"""
FastAPI application factory for ScanbonAI.

Startup sequence
----------------
1. Configure structured logging (structlog + stdlib logging bridge).
2. Create the FastAPI app instance with metadata.
3. Register ASGI middleware (order matters – outermost first):
   a. CORSMiddleware
   b. CorrelationIDMiddleware
   c. TenantIsolationMiddleware
4. Include all API routers.
5. Register startup / shutdown lifespan handlers for DB and Redis.
6. Mount the health-check endpoint.

Running locally
---------------
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Production
----------
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.config import settings
from app.database import connect_db, disconnect_db
from app.middleware import CorrelationIDMiddleware, TenantIsolationMiddleware
from app.routers import admin, admin_clients, auth, billing, invoices, signed_links, webhooks, whatsapp_admin
from app.schemas import ErrorResponse

# ---------------------------------------------------------------------------
# Structured logging configuration
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """
    Set up structlog to emit JSON log lines in production.

    In development (when LOG_LEVEL is DEBUG) the console renderer is used
    instead so logs are human-readable in the terminal.
    """
    is_debug = settings.LOG_LEVEL == "DEBUG"

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_debug:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL, logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging → structlog so third-party libs (SQLAlchemy,
    # httpx, celery) also emit structured log lines.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.LOG_LEVEL)


_configure_logging()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Redis connection (module-level; initialised in lifespan)
# ---------------------------------------------------------------------------

_redis_client: aioredis.Redis | None = None


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application-level resources that must be opened once and closed
    on shutdown (database connection pool, Redis client, etc.).

    FastAPI calls this once on startup; yield control; then calls cleanup
    on shutdown.
    """
    global _redis_client

    # --- Startup ---
    logger.info("app.startup", log_level=settings.LOG_LEVEL)

    # Database
    try:
        await connect_db()
    except Exception as exc:
        logger.critical("app.startup.db_failed", error=str(exc))
        raise

    # Redis
    try:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await _redis_client.ping()
        logger.info("app.startup.redis_connected", url=settings.REDIS_URL[:20] + "...")
    except Exception as exc:
        logger.warning(
            "app.startup.redis_unavailable",
            error=str(exc),
            note="Application will start without Redis; Celery tasks may fail.",
        )

    logger.info("app.startup.complete")
    yield

    # --- Shutdown ---
    logger.info("app.shutdown")

    await disconnect_db()

    if _redis_client:
        await _redis_client.aclose()
        logger.info("app.shutdown.redis_closed")

    logger.info("app.shutdown.complete")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """
    Construct and return the configured FastAPI application.

    Separating creation into a factory function makes it straightforward to
    instantiate the app in tests with different settings.
    """
    app = FastAPI(
        title="ScanbonAI",
        description=(
            "Tax Administration via WhatsApp invoices. "
            "Submit invoice images, get structured extraction, correct and confirm."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # Middleware (added outermost → innermost)
    # ------------------------------------------------------------------

    # 1. CORS – allow the SPA and WhatsApp webhook calls through
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # FUTURE: restrict to known origins via settings
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # 2. Correlation ID – inject X-Request-ID into every request
    app.add_middleware(CorrelationIDMiddleware)

    # 3. Tenant isolation – resolve tenant_id and bind to request.state
    app.add_middleware(TenantIsolationMiddleware)

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    app.include_router(webhooks.router)
    app.include_router(invoices.router)
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(signed_links.router)
    app.include_router(whatsapp_admin.router)
    app.include_router(billing.router)
    app.include_router(admin_clients.router)

    # ------------------------------------------------------------------
    # Prometheus metrics endpoint
    # ------------------------------------------------------------------
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    @app.get(
        "/health",
        tags=["ops"],
        summary="Health check",
        response_model=dict,
    )
    async def health_check(request: Request) -> dict:
        """
        Liveness / readiness probe endpoint.

        Returns a 200 with basic status information when the application is
        running.  Does NOT check downstream dependencies (DB, Redis) to keep
        liveness probes fast.  Use a dedicated readiness probe for that.
        """
        return {
            "status": "ok",
            "version": "1.0.0",
            "request_id": getattr(request.state, "request_id", None),
        }

    @app.get(
        "/health/ready",
        tags=["ops"],
        summary="Readiness check",
        response_model=dict,
    )
    async def readiness_check() -> dict:
        """
        Deep readiness check that validates DB and Redis connectivity.

        Returns 503 if any dependency is unavailable so the load balancer
        can remove the instance from rotation.
        """
        import sqlalchemy

        checks: dict[str, str] = {}
        overall_ok = True

        # Database
        try:
            from app.database import _engine
            async with _engine.connect() as conn:
                await conn.execute(sqlalchemy.text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            overall_ok = False

        # Redis
        try:
            if _redis_client:
                await _redis_client.ping()
                checks["redis"] = "ok"
            else:
                checks["redis"] = "not_initialized"
                overall_ok = False
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
            overall_ok = False

        status_code = status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=status_code,
            content={"status": "ready" if overall_ok else "not_ready", "checks": checks},
        )

    # ------------------------------------------------------------------
    # Global exception handlers
    # ------------------------------------------------------------------

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Catch-all handler for unhandled exceptions.

        Logs the full traceback and returns a generic 500 response so that
        internal error details are never leaked to API clients.
        """
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "app.unhandled_exception",
            exc_info=exc,
            path=str(request.url.path),
            request_id=request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="Internal server error.",
                request_id=request_id,
            ).model_dump(),
        )

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by uvicorn)
# ---------------------------------------------------------------------------
app: FastAPI = create_app()
