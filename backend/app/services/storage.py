"""
File storage service for ScanbonAI invoice images.

Storage layout
--------------
All files are stored under ``settings.STORAGE_PATH`` with the following
directory structure::

    {STORAGE_PATH}/
    └── {tenant_id}/
        └── {user_id}/
            └── {YYYY-MM}/
                └── {invoice_id}.{ext}

This layout makes it straightforward to:
- Enforce tenant isolation at the filesystem level.
- List or export all invoices for a specific month.
- Apply directory-level permissions or S3 bucket policies.

FUTURE: Replace the local filesystem backend with an S3-compatible adapter
(MinIO, AWS S3, GCS).  The public interface (``save_invoice_image``,
``get_image_path``) will remain stable.
"""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum allowed image size enforced by this layer as a safety net.
# The primary check is at the API layer using settings.MAX_IMAGE_SIZE_MB.
_MAX_BYTES: Final[int] = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024

# Allowed MIME types for invoice images
_ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/tiff",
        "application/pdf",
    }
)

# Extension to use when the MIME type cannot be determined
_FALLBACK_EXT: Final[str] = ".jpg"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_invoice_image(
    tenant_id: str,
    user_id: str,
    image_bytes: bytes,
    invoice_id: str,
    mime_type: str | None = None,
) -> str:
    """
    Persist raw image bytes to the storage directory and return the relative path.

    Parameters
    ----------
    tenant_id:
        UUID of the owning tenant.  Used as the top-level directory segment.
    user_id:
        UUID of the user who submitted the image.
    image_bytes:
        Raw image bytes (JPEG, PNG, WEBP, PDF, etc.).
    invoice_id:
        UUID of the invoice record.  Used as the file name (without extension).
    mime_type:
        Optional MIME type hint.  If omitted, guessed from the magic bytes.

    Returns
    -------
    str
        The relative storage path, e.g.
        ``"{tenant_id}/{user_id}/2024-03/{invoice_id}.jpg"``.
        This value should be stored in ``invoices.storage_path``.

    Raises
    ------
    ValueError
        If the file size exceeds ``MAX_IMAGE_SIZE_MB`` or the MIME type is
        not in the allowed list.
    OSError
        If the directory cannot be created or the file cannot be written.
    """
    # --- Size guard ---
    if len(image_bytes) > _MAX_BYTES:
        size_mb = len(image_bytes) / (1024 * 1024)
        raise ValueError(
            f"Image size {size_mb:.1f} MB exceeds the maximum of "
            f"{settings.MAX_IMAGE_SIZE_MB} MB."
        )

    # --- MIME type resolution ---
    resolved_mime = _resolve_mime(image_bytes, mime_type)
    if resolved_mime not in _ALLOWED_MIME_TYPES:
        raise ValueError(
            f"Unsupported image MIME type: {resolved_mime!r}. "
            f"Allowed types: {sorted(_ALLOWED_MIME_TYPES)}"
        )

    ext = _ext_for_mime(resolved_mime)

    # --- Path construction ---
    month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    rel_path = f"{tenant_id}/{user_id}/{month}/{invoice_id}{ext}"
    abs_path = Path(settings.STORAGE_PATH) / rel_path

    # --- Directory creation ---
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Write (atomic via temp file rename) ---
    tmp_path = abs_path.with_suffix(ext + ".tmp")
    try:
        tmp_path.write_bytes(image_bytes)
        tmp_path.rename(abs_path)
    except Exception:
        # Clean up temp file on failure
        tmp_path.unlink(missing_ok=True)
        raise

    logger.info(
        "storage.save.complete",
        tenant_id=tenant_id,
        user_id=user_id,
        invoice_id=invoice_id,
        path=rel_path,
        size_bytes=len(image_bytes),
        mime_type=resolved_mime,
    )

    return rel_path


def get_image_path(
    tenant_id: str,
    user_id: str,
    month: str,
    invoice_id: str,
    ext: str | None = None,
) -> str | None:
    """
    Locate the stored image for a given invoice and return its absolute path.

    The function searches common extensions when ``ext`` is not provided.

    Parameters
    ----------
    tenant_id:
        Owning tenant UUID.
    user_id:
        Owning user UUID.
    month:
        Month directory in ``YYYY-MM`` format.
    invoice_id:
        Invoice UUID (file name without extension).
    ext:
        Optional file extension (with dot, e.g. ``".jpg"``).
        If ``None``, the function tries all known extensions.

    Returns
    -------
    str | None
        Absolute path to the image file, or ``None`` if not found.
    """
    base_dir = Path(settings.STORAGE_PATH) / tenant_id / user_id / month
    extensions_to_try = (
        [ext] if ext else [".jpg", ".jpeg", ".png", ".webp", ".tiff", ".pdf"]
    )

    for candidate_ext in extensions_to_try:
        candidate = base_dir / f"{invoice_id}{candidate_ext}"
        if candidate.exists():
            logger.debug(
                "storage.get_path.found",
                path=str(candidate),
                invoice_id=invoice_id,
            )
            return str(candidate)

    logger.debug(
        "storage.get_path.not_found",
        tenant_id=tenant_id,
        user_id=user_id,
        month=month,
        invoice_id=invoice_id,
    )
    return None


def resolve_abs_path(relative_path: str) -> str:
    """
    Convert a relative storage path to an absolute filesystem path.

    Parameters
    ----------
    relative_path:
        A path returned by ``save_invoice_image``, e.g.
        ``"{tenant_id}/{user_id}/2024-03/{invoice_id}.jpg"``.

    Returns
    -------
    str
        Absolute path under ``settings.STORAGE_PATH``.
    """
    return str(Path(settings.STORAGE_PATH) / relative_path)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_mime(image_bytes: bytes, hint: str | None) -> str:
    """
    Determine the MIME type of an image.

    Resolution order:
    1. Caller-provided ``hint``.
    2. Magic-byte sniffing (first 12 bytes).
    3. Fallback to ``"image/jpeg"``.
    """
    if hint:
        return hint.lower()

    header = image_bytes[:12]

    # JPEG: FF D8 FF
    if header[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # WebP: RIFF????WEBP
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    # PDF: %PDF
    if header[:4] == b"%PDF":
        return "application/pdf"
    # TIFF: II (little-endian) or MM (big-endian)
    if header[:2] in (b"II", b"MM"):
        return "image/tiff"

    logger.warning("storage.mime.unknown_magic", prefix=header.hex())
    return "image/jpeg"


def _ext_for_mime(mime_type: str) -> str:
    """Return the preferred file extension for a given MIME type."""
    _MAP = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/tiff": ".tiff",
        "application/pdf": ".pdf",
    }
    ext = _MAP.get(mime_type)
    if ext is None:
        guessed = mimetypes.guess_extension(mime_type, strict=False)
        ext = guessed or _FALLBACK_EXT
    return ext
