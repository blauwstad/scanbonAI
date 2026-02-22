"""
WhatsApp Business Cloud API client for ScanbonAI (multi-tenant).

Responsibilities
----------------
- Download media blobs referenced in inbound webhook events.
- Send plain-text messages back to users.
- Mark inbound messages as read.
- Validate inbound webhook payloads using HMAC-SHA256 signature verification.
- Encrypt / decrypt per-tenant access tokens at rest (Fernet).
- Resolve tenant credentials from the database and return a configured client.

All HTTP calls use ``httpx.AsyncClient`` with configurable timeouts and are
wrapped with ``tenacity`` retry logic for transient network failures.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx
import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = structlog.get_logger(__name__)

# Default timeouts (seconds)
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 30.0

# ---------------------------------------------------------------------------
# Fernet encryption helpers
# ---------------------------------------------------------------------------


def _get_fernet() -> Fernet:
    """Return a Fernet instance using the application-wide encryption key.

    Raises ``ValueError`` if ``TOKEN_ENCRYPTION_KEY`` is not configured.
    """
    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        raise ValueError(
            "TOKEN_ENCRYPTION_KEY is not set. "
            "Generate one with generate_encryption_key() and add it to your .env file."
        )
    return Fernet(key.encode())


def encrypt_token(plaintext: str) -> str:
    """Encrypt a plaintext access token and return a URL-safe base-64 ciphertext string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext string back to the original access token.

    Raises ``cryptography.fernet.InvalidToken`` on tampered or invalid data.
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("whatsapp.token_decrypt_failed", hint="check TOKEN_ENCRYPTION_KEY")
        raise


def generate_encryption_key() -> str:
    """Generate a new Fernet key suitable for ``TOKEN_ENCRYPTION_KEY``.

    This is a utility helper -- call it once during initial setup, then
    store the returned value in your environment / secrets manager.
    """
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# WhatsApp Cloud API client (multi-tenant)
# ---------------------------------------------------------------------------


class WhatsAppClient:
    """
    Async client for the WhatsApp Cloud API.

    Each instance is bound to a single tenant's credentials.  Construct it
    with the tenant's plaintext ``access_token`` and ``phone_number_id``.

    Intended to be used as an async context manager::

        async with WhatsAppClient(access_token="...", phone_number_id="...") as wa:
            image_bytes = await wa.download_media("MEDIA_ID_HERE")
    """

    def __init__(self, *, access_token: str, phone_number_id: str) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._client: httpx.AsyncClient | None = None

        version = getattr(settings, "META_GRAPH_API_VERSION", "v21.0") or "v21.0"
        self._base_url = f"https://graph.facebook.com/{version}"

    # -- async context manager ------------------------------------------------

    async def __aenter__(self) -> WhatsAppClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_READ_TIMEOUT,
                write=10.0,
                pool=5.0,
            ),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- internals ------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "WhatsAppClient must be used as an async context manager "
                "or _client must be initialised."
            )
        return self._client

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def download_media(self, media_id: str) -> bytes:
        """
        Download raw bytes for a WhatsApp media object.

        The Cloud API requires two calls:
        1. GET ``/{media_id}`` -> retrieve the temporary CDN URL.
        2. GET CDN URL -> download the actual bytes.

        Parameters
        ----------
        media_id:
            The ``id`` field from the WhatsApp webhook message's media block.

        Returns
        -------
        bytes
            Raw image/document bytes.

        Raises
        ------
        httpx.HTTPStatusError
            If either API call returns a non-2xx status code.
        """
        client = self._ensure_client()
        log = logger.bind(media_id=media_id)

        # Step 1 -- resolve the CDN URL
        log.debug("whatsapp.media.resolve_start")
        t0 = time.monotonic()
        resp = await client.get(f"/{media_id}")
        resp.raise_for_status()
        media_data: dict[str, Any] = resp.json()
        cdn_url: str = media_data["url"]
        log.debug("whatsapp.media.resolved", cdn_url_prefix=cdn_url[:50])

        # Step 2 -- download bytes from CDN URL
        # CDN requires the same Authorization header, so we reuse our client.
        dl_resp = await client.get(cdn_url)
        dl_resp.raise_for_status()
        raw_bytes = dl_resp.content
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        log.info(
            "whatsapp.media.downloaded",
            size_bytes=len(raw_bytes),
            elapsed_ms=elapsed_ms,
        )
        return raw_bytes

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def send_text_message(self, phone: str, text: str) -> dict[str, Any]:
        """
        Send a plain-text WhatsApp message to a phone number.

        Parameters
        ----------
        phone:
            Recipient E.164 phone number (without the '+' prefix as
            required by the Cloud API, e.g. ``"254700000000"``).
        text:
            Message body (max 4096 characters per WhatsApp limits).

        Returns
        -------
        dict
            The raw API response JSON.

        Raises
        ------
        httpx.HTTPStatusError
            On non-2xx responses.
        ValueError
            If ``text`` exceeds 4096 characters.
        """
        if len(text) > 4096:
            raise ValueError(
                f"WhatsApp text message must be <= 4096 chars, got {len(text)}."
            )

        client = self._ensure_client()
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        log = logger.bind(to_phone=phone[:4] + "****")
        log.debug("whatsapp.message.send_start")

        resp = await client.post(
            f"/{self._phone_number_id}/messages",
            json=payload,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        log.info(
            "whatsapp.message.sent",
            message_id=data.get("messages", [{}])[0].get("id"),
        )
        return data

    # ------------------------------------------------------------------
    # Read receipts
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def mark_as_read(self, message_id: str) -> dict[str, Any]:
        """
        Mark an inbound message as read (blue ticks).

        Parameters
        ----------
        message_id:
            The WhatsApp message ID from the inbound webhook event.

        Returns
        -------
        dict
            The raw API response JSON.

        Raises
        ------
        httpx.HTTPStatusError
            On non-2xx responses.
        """
        client = self._ensure_client()
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }

        log = logger.bind(message_id=message_id)
        log.debug("whatsapp.message.mark_read_start")

        resp = await client.post(
            f"/{self._phone_number_id}/messages",
            json=payload,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        log.info("whatsapp.message.marked_read")
        return data


# ---------------------------------------------------------------------------
# Webhook signature verification (standalone functions)
# ---------------------------------------------------------------------------


def verify_webhook_signature(
    payload: bytes,
    signature_header: str,
    *,
    app_secret: str,
) -> bool:
    """
    Validate an inbound webhook payload against the X-Hub-Signature-256 header.

    Meta signs every webhook delivery with HMAC-SHA256 using the **Meta App
    Secret** (not the access token).

    Parameters
    ----------
    payload:
        Raw request body bytes (do **not** decode before passing in).
    signature_header:
        Value of the ``X-Hub-Signature-256`` header, e.g.
        ``"sha256=abc123..."``.
    app_secret:
        The Meta App Secret for the WhatsApp Business app.

    Returns
    -------
    bool
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    if not signature_header.startswith("sha256="):
        logger.warning("whatsapp.webhook.bad_signature_format")
        return False

    expected_hash = signature_header[len("sha256="):]
    computed_hash = hmac.new(
        key=app_secret.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    is_valid = hmac.compare_digest(computed_hash, expected_hash)
    if not is_valid:
        logger.warning("whatsapp.webhook.signature_mismatch")
    return is_valid


def verify_webhook_signature_from_settings(
    payload: bytes,
    signature_header: str,
) -> bool:
    """Convenience wrapper that reads the app secret from ``settings.META_APP_SECRET``."""
    return verify_webhook_signature(
        payload,
        signature_header,
        app_secret=settings.META_APP_SECRET,
    )


# ---------------------------------------------------------------------------
# Connection test (used by admin settings UI)
# ---------------------------------------------------------------------------


async def test_connection(access_token: str, phone_number_id: str) -> dict[str, Any]:
    """
    Verify WhatsApp credentials by fetching the phone number profile.

    Makes a GET request to
    ``/{phone_number_id}?fields=display_phone_number,verified_name,quality_rating``
    and returns the JSON response.

    Parameters
    ----------
    access_token:
        Plaintext access token for the WhatsApp Cloud API.
    phone_number_id:
        The numeric phone number ID registered in the WhatsApp Business account.

    Returns
    -------
    dict
        JSON payload including ``display_phone_number``, ``verified_name``,
        and ``quality_rating``.

    Raises
    ------
    httpx.HTTPStatusError
        If the API returns a non-2xx status code (e.g. invalid credentials).
    """
    version = getattr(settings, "META_GRAPH_API_VERSION", "v21.0") or "v21.0"
    base_url = f"https://graph.facebook.com/{version}"

    async with httpx.AsyncClient(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        timeout=httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=10.0,
            pool=5.0,
        ),
    ) as client:
        resp = await client.get(
            f"/{phone_number_id}",
            params={"fields": "display_phone_number,verified_name,quality_rating"},
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        logger.info(
            "whatsapp.test_connection.ok",
            phone_number_id=phone_number_id,
            verified_name=data.get("verified_name"),
        )
        return data


# ---------------------------------------------------------------------------
# Tenant factory
# ---------------------------------------------------------------------------


async def get_whatsapp_client_for_tenant(
    db: AsyncSession,
    tenant_id: str,
) -> WhatsAppClient:
    """
    Look up WhatsApp credentials for a tenant and return a configured client.

    The returned ``WhatsAppClient`` is **not** yet entered as an async context
    manager -- the caller must use ``async with client: ...``.

    Parameters
    ----------
    db:
        An active async database session.
    tenant_id:
        The UUID of the tenant whose WhatsApp credentials should be loaded.

    Returns
    -------
    WhatsAppClient
        A client pre-configured with the tenant's decrypted access token
        and phone number ID.

    Raises
    ------
    ValueError
        If no active WhatsApp settings are found for the given tenant.
    """
    from app.models import WhatsAppSettings  # deferred to avoid circular imports

    stmt = (
        select(WhatsAppSettings)
        .where(
            WhatsAppSettings.tenant_id == tenant_id,
            WhatsAppSettings.is_active.is_(True),
        )
    )
    result = await db.execute(stmt)
    wa_settings = result.scalar_one_or_none()

    if wa_settings is None:
        raise ValueError(
            f"No active WhatsApp settings found for tenant {tenant_id!r}"
        )

    plaintext_token = decrypt_token(wa_settings.access_token_encrypted)

    logger.debug(
        "whatsapp.client_factory.resolved",
        tenant_id=tenant_id,
        phone_number_id=wa_settings.phone_number_id,
    )

    return WhatsAppClient(
        access_token=plaintext_token,
        phone_number_id=wa_settings.phone_number_id,
    )
