"""
Signed URL generation and validation for ScanbonAI.

Signed URLs allow WhatsApp users to access invoice images and correction forms
without a full authentication session.  They are time-limited and cryptographically
signed using HMAC-SHA256 (via ``itsdangerous.URLSafeTimedSerializer``).

Token payload structure
-----------------------
{
  "invoice_id": "<uuid>",
  "user_id":    "<uuid>",
  "tenant_id":  "<uuid>",
  "link_type":  "view_image" | "correction_form" | "confirm"
}

Security notes
--------------
- Tokens are signed with ``settings.SIGNING_KEY`` (separate from ``SECRET_KEY``
  so that key rotation can be scoped to signed URLs without invalidating sessions).
- Expiry is enforced by ``itsdangerous`` using a timestamp embedded in the token.
- Tokens are stored in the ``signed_links`` table for audit purposes; validation
  does NOT require a DB lookup (stateless), but the table enables token revocation
  in future if required.
"""

from __future__ import annotations

from typing import Any

import structlog
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from app.config import settings
from app.models import SignedLinkType

logger = structlog.get_logger(__name__)

# Salt scopes the HMAC key to this specific use-case; prevents tokens generated
# for one purpose being reused for another.
_SALT = "scanbonai.signed-link.v1"


def _get_serializer() -> URLSafeTimedSerializer:
    """Return a configured ``URLSafeTimedSerializer`` instance."""
    return URLSafeTimedSerializer(
        secret_key=settings.SIGNING_KEY,
        salt=_SALT,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_signed_token(
    invoice_id: str,
    user_id: str,
    tenant_id: str,
    link_type: SignedLinkType,
    expiry_seconds: int | None = None,
) -> str:
    """
    Generate a signed, time-limited token for secure resource access.

    Parameters
    ----------
    invoice_id:
        UUID of the invoice this token grants access to.
    user_id:
        UUID of the user the token is issued for.
    tenant_id:
        UUID of the tenant that owns the invoice.
    link_type:
        The type of resource the token allows access to.
    expiry_seconds:
        Token lifetime in seconds.  Defaults to ``settings.SIGNED_URL_EXPIRY_SECONDS``.

    Returns
    -------
    str
        URL-safe signed token string suitable for embedding in a link or QR code.

    Example
    -------
    ::

        token = generate_signed_token(
            invoice_id="...",
            user_id="...",
            tenant_id="...",
            link_type=SignedLinkType.VIEW_IMAGE,
        )
        # Embed as: https://app.example.com/api/v1/signed/{token}
    """
    payload: dict[str, str] = {
        "invoice_id": invoice_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "link_type": link_type.value,
    }

    serializer = _get_serializer()
    token: str = serializer.dumps(payload)

    logger.debug(
        "signed_url.generated",
        invoice_id=invoice_id,
        user_id=user_id,
        tenant_id=tenant_id,
        link_type=link_type.value,
    )
    return token


def validate_signed_token(token: str) -> dict[str, Any]:
    """
    Validate a signed token and return its payload.

    Parameters
    ----------
    token:
        The signed token string to validate.

    Returns
    -------
    dict
        The decoded payload::

            {
              "invoice_id": "...",
              "user_id":    "...",
              "tenant_id":  "...",
              "link_type":  "view_image"
            }

    Raises
    ------
    ValueError
        If the token has expired (with message ``"Token has expired."``).
    ValueError
        If the token is invalid, tampered with, or cannot be decoded
        (with message ``"Invalid token."``).
    """
    serializer = _get_serializer()
    max_age = settings.SIGNED_URL_EXPIRY_SECONDS

    try:
        payload: dict[str, Any] = serializer.loads(token, max_age=max_age)
    except SignatureExpired:
        logger.warning("signed_url.expired")
        raise ValueError("Token has expired.")
    except BadData as exc:
        logger.warning("signed_url.invalid", error=str(exc))
        raise ValueError("Invalid token.")

    # Sanity-check required fields are present
    required = {"invoice_id", "user_id", "tenant_id", "link_type"}
    missing = required - payload.keys()
    if missing:
        logger.warning("signed_url.missing_fields", missing=list(missing))
        raise ValueError("Invalid token.")

    logger.debug(
        "signed_url.validated",
        invoice_id=payload.get("invoice_id"),
        link_type=payload.get("link_type"),
    )
    return payload


def build_signed_url(
    base_url: str,
    invoice_id: str,
    user_id: str,
    tenant_id: str,
    link_type: SignedLinkType,
) -> str:
    """
    Convenience helper that generates a token and formats it as a full URL.

    Parameters
    ----------
    base_url:
        Application base URL, e.g. ``"https://app.scanbonai.com"``.
    invoice_id, user_id, tenant_id, link_type:
        Forwarded to ``generate_signed_token``.

    Returns
    -------
    str
        Full signed URL, e.g.
        ``"https://app.scanbonai.com/api/v1/signed/<token>"``
    """
    token = generate_signed_token(
        invoice_id=invoice_id,
        user_id=user_id,
        tenant_id=tenant_id,
        link_type=link_type,
    )
    return f"{base_url.rstrip('/')}/api/v1/signed/{token}"
