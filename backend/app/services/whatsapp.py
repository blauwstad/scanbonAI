"""
WhatsApp Business Cloud API client for ScanbonAI.

Responsibilities
----------------
- Download media blobs referenced in inbound webhook events.
- Send plain-text messages back to users.
- Validate inbound webhook payloads using HMAC-SHA256 signature verification.

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
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = structlog.get_logger(__name__)

# WhatsApp Cloud API base URL
_WA_BASE_URL = "https://graph.facebook.com/v20.0"

# Default timeouts (seconds)
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 30.0


def _build_client() -> httpx.AsyncClient:
    """Construct a pre-configured httpx async client for the WhatsApp API."""
    return httpx.AsyncClient(
        base_url=_WA_BASE_URL,
        headers={
            "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=10.0, pool=5.0),
        follow_redirects=True,
    )


class WhatsAppClient:
    """
    Async client for the WhatsApp Cloud API.

    Intended to be used as an async context manager or instantiated once
    per application lifecycle and shared via FastAPI dependency injection.

    Example::

        async with WhatsAppClient() as wa:
            image_bytes = await wa.download_media("MEDIA_ID_HERE")
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "WhatsAppClient":
        self._client = _build_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

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
        1. GET ``/{media_id}`` → retrieve the temporary CDN URL.
        2. GET CDN URL → download the actual bytes.

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

        # Step 1 – resolve the CDN URL
        log.debug("whatsapp.media.resolve_start")
        t0 = time.monotonic()
        resp = await client.get(f"/{media_id}")
        resp.raise_for_status()
        media_data: dict[str, Any] = resp.json()
        cdn_url: str = media_data["url"]
        log.debug("whatsapp.media.resolved", cdn_url_prefix=cdn_url[:50])

        # Step 2 – download bytes from CDN URL
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
                f"WhatsApp text message must be ≤ 4096 chars, got {len(text)}."
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
            f"/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
            json=payload,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        log.info("whatsapp.message.sent", message_id=data.get("messages", [{}])[0].get("id"))
        return data

    # ------------------------------------------------------------------
    # Webhook signature validation
    # ------------------------------------------------------------------

    @staticmethod
    def verify_webhook_signature(payload: bytes, signature_header: str) -> bool:
        """
        Validate an inbound webhook payload against the X-Hub-Signature-256 header.

        WhatsApp signs every webhook delivery with HMAC-SHA256 using the
        app's ``WHATSAPP_API_TOKEN`` as the key (not the verify token).

        Parameters
        ----------
        payload:
            Raw request body bytes (do **not** decode before passing in).
        signature_header:
            Value of the ``X-Hub-Signature-256`` header, e.g.
            ``"sha256=abc123..."``

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
            key=settings.WHATSAPP_API_TOKEN.encode(),
            msg=payload,
            digestmod=hashlib.sha256,
        ).hexdigest()

        is_valid = hmac.compare_digest(computed_hash, expected_hash)
        if not is_valid:
            logger.warning("whatsapp.webhook.signature_mismatch")
        return is_valid


# ---------------------------------------------------------------------------
# Module-level singleton (created lazily; use via context manager in routes)
# ---------------------------------------------------------------------------

def get_whatsapp_client() -> WhatsAppClient:
    """FastAPI dependency that yields a WhatsAppClient instance."""
    return WhatsAppClient()
