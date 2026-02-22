"""
OpenClaw gateway client for ScanbonAI.

Replaces the Meta WhatsApp Cloud API for outbound messaging.  When the OCR
worker finishes processing an invoice it calls ``OpenClawClient.send_message``
to deliver the results back to the user via the OpenClaw gateway.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

# Timeouts (seconds)
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 15.0


class OpenClawClient:
    """Send messages to WhatsApp users via the OpenClaw gateway."""

    def __init__(self) -> None:
        self._base_url = settings.OPENCLAW_GATEWAY_URL  # e.g. "http://openclaw:18789"
        self._token = settings.OPENCLAW_GATEWAY_TOKEN

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=10.0,
            pool=5.0,
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_message(self, phone: str, text: str) -> dict[str, Any]:
        """Send a text message to a WhatsApp user via OpenClaw.

        Tries ``POST /hooks/wake`` first.  If the gateway returns a 404 for
        that endpoint (older / differently-configured OpenClaw builds), it
        falls back to ``POST /hooks/agent``.

        A single automatic retry is attempted on transient network errors.

        Parameters
        ----------
        phone:
            Recipient phone number (E.164 without the ``+`` prefix,
            e.g. ``"254700000000"``).
        text:
            Message body to deliver.

        Returns
        -------
        dict
            The parsed JSON response from the gateway.

        Raises
        ------
        httpx.HTTPStatusError
            On non-2xx responses (other than 404 during the fallback logic).
        httpx.TimeoutException | httpx.NetworkError
            When the gateway is unreachable after the retry.
        """
        payload = {
            "text": text,
            "deliver": True,
            "channel": "whatsapp",
            "to": phone,
        }

        log = logger.bind(to_phone=phone[:4] + "****" if len(phone) > 4 else phone)

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout(),
        ) as client:
            # --- attempt /hooks/wake (with one retry on transient errors) ---
            for attempt in range(2):
                try:
                    log.debug("openclaw.send_message.start", endpoint="/hooks/wake", attempt=attempt + 1)
                    resp = await client.post("/hooks/wake", json=payload)

                    if resp.status_code == 404 and attempt == 0:
                        # Endpoint not available -- fall through to /hooks/agent
                        log.debug("openclaw.send_message.wake_not_found, falling back")
                        break

                    resp.raise_for_status()
                    data: dict[str, Any] = resp.json()
                    log.info("openclaw.send_message.sent", endpoint="/hooks/wake")
                    return data

                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == 0:
                        log.warning("openclaw.send_message.transient_error", error=str(exc), attempt=attempt + 1)
                        continue
                    log.error("openclaw.send_message.failed", error=str(exc))
                    raise

            # --- fallback: /hooks/agent (with one retry) ---
            for attempt in range(2):
                try:
                    log.debug("openclaw.send_message.start", endpoint="/hooks/agent", attempt=attempt + 1)
                    resp = await client.post("/hooks/agent", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    log.info("openclaw.send_message.sent", endpoint="/hooks/agent")
                    return data

                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == 0:
                        log.warning("openclaw.send_message.transient_error", error=str(exc), attempt=attempt + 1)
                        continue
                    log.error("openclaw.send_message.failed", error=str(exc))
                    raise

        # Should never reach here, but satisfy the type checker.
        raise RuntimeError("send_message: exhausted all endpoints")  # pragma: no cover

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def check_health(self) -> dict[str, Any]:
        """Check whether the OpenClaw gateway is reachable.

        Performs a ``GET /health`` request with a short timeout.

        Returns
        -------
        dict
            ``{"healthy": True, "details": <response JSON>}`` on success, or
            ``{"healthy": False, "error": "<description>"}`` on failure.
        """
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers(),
                timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0),
            ) as client:
                resp = await client.get("/health")
                resp.raise_for_status()
                details = resp.json()
                logger.info("openclaw.health.ok")
                return {"healthy": True, "details": details}

        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("openclaw.health.failed", error=str(exc))
            return {"healthy": False, "error": str(exc)}
        except Exception as exc:
            logger.error("openclaw.health.unexpected_error", error=str(exc))
            return {"healthy": False, "error": str(exc)}
