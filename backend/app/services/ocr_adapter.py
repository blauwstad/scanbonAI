"""
OCR adapter interface and concrete implementations for ScanbonAI.

Design
------
``OCRAdapter`` is an abstract base class that defines the contract every OCR
backend must satisfy.  New providers (Google Vision, Azure DI, etc.) only need
to implement ``extract_text``.

The ``OCRResult`` dataclass is the common return type so that downstream
services (``extraction.py``, the Celery worker) never depend on
provider-specific response shapes.

Current implementations
-----------------------
- ``DeepSeekOCR2Adapter`` – calls the DeepSeek OCR v2 REST API via httpx.
"""

from __future__ import annotations

import base64
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------


@dataclass
class OCRResult:
    """
    Provider-agnostic OCR output.

    Attributes
    ----------
    raw_text:
        Full text extracted from the image, as a single string.
    confidence:
        Overall confidence in [0.0, 1.0].  ``None`` when the provider does
        not report a confidence score.
    raw_response:
        Unmodified JSON response from the provider for debugging.
    engine:
        Short identifier for the OCR engine used, e.g. ``"deepseek_v2"``.
    processing_ms:
        Wall-clock time the API call took.
    pages:
        Per-page text when the document has multiple pages (PDFs, etc.).
    """

    raw_text: str
    confidence: float | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    engine: str = "unknown"
    processing_ms: int = 0
    pages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class OCRAdapter(ABC):
    """
    Abstract OCR adapter.

    All concrete adapters must implement ``extract_text``.  They may also
    override ``close`` if they hold a persistent HTTP client or connection pool.
    """

    @abstractmethod
    async def extract_text(self, image_path: str | Path) -> OCRResult:
        """
        Extract text from an image file.

        Parameters
        ----------
        image_path:
            Absolute path to the image on disk (JPEG, PNG, WEBP, PDF).

        Returns
        -------
        OCRResult
            Parsed result with at minimum ``raw_text`` populated.

        Raises
        ------
        FileNotFoundError
            If ``image_path`` does not exist.
        httpx.HTTPStatusError
            On non-2xx API responses.
        """

    async def close(self) -> None:
        """Release any held resources (HTTP clients, etc.)."""


# ---------------------------------------------------------------------------
# DeepSeek OCR v2
# ---------------------------------------------------------------------------


class DeepSeekOCR2Adapter(OCRAdapter):
    """
    OCR adapter for the DeepSeek OCR v2 REST API.

    The API accepts a base64-encoded image and returns structured text
    extraction with optional per-region confidence scores.

    API reference (internal):
        POST {DEEPSEEK_OCR_API_URL}
        Authorization: Bearer {DEEPSEEK_OCR_API_KEY}
        Body: {"image": "<base64>", "language": "auto"}

    Usage::

        async with DeepSeekOCR2Adapter() as adapter:
            result = await adapter.extract_text("/path/to/invoice.jpg")
            print(result.raw_text)
    """

    ENGINE_ID = "deepseek_v2"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_OCR_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=5.0),
        )

    async def __aenter__(self) -> "DeepSeekOCR2Adapter":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def extract_text(self, image_path: str | Path) -> OCRResult:
        """
        Submit an image to the DeepSeek OCR v2 API and return the result.

        Parameters
        ----------
        image_path:
            Absolute path to the image file.

        Returns
        -------
        OCRResult
            Contains ``raw_text``, ``confidence``, and the full API response.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found for OCR: {path}")

        log = logger.bind(image_path=str(path), engine=self.ENGINE_ID)
        log.debug("ocr.extract.start")

        # Read and base64-encode the image
        image_bytes = path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("ascii")

        # Detect MIME type from extension
        suffix = path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
        }
        mime_type = mime_map.get(suffix, "image/jpeg")

        request_body: dict[str, Any] = {
            "image": f"data:{mime_type};base64,{b64_image}",
            "language": "auto",
            "output_format": "text",
        }

        t0 = time.monotonic()
        response = await self._client.post(
            settings.DEEPSEEK_OCR_API_URL,
            json=request_body,
        )
        response.raise_for_status()
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        raw_response: dict[str, Any] = response.json()

        # Parse provider-specific response shape
        raw_text, confidence, pages = self._parse_response(raw_response)

        log.info(
            "ocr.extract.complete",
            text_len=len(raw_text),
            confidence=confidence,
            elapsed_ms=elapsed_ms,
        )

        return OCRResult(
            raw_text=raw_text,
            confidence=confidence,
            raw_response=raw_response,
            engine=self.ENGINE_ID,
            processing_ms=elapsed_ms,
            pages=pages,
        )

    @staticmethod
    def _parse_response(
        data: dict[str, Any],
    ) -> tuple[str, float | None, list[str]]:
        """
        Extract ``(raw_text, confidence, pages)`` from the DeepSeek API response.

        The exact response schema may differ across API versions; this method
        attempts several common shapes gracefully.
        """
        # Common shape 1: {"text": "...", "confidence": 0.98}
        if "text" in data:
            raw_text: str = data["text"]
            confidence: float | None = data.get("confidence")
            return raw_text, confidence, []

        # Common shape 2: {"pages": [{"text": "...", "confidence": 0.97}]}
        if "pages" in data and isinstance(data["pages"], list):
            page_texts: list[str] = []
            confidences: list[float] = []
            for page in data["pages"]:
                page_texts.append(page.get("text", ""))
                if "confidence" in page:
                    confidences.append(float(page["confidence"]))
            raw_text = "\n\n".join(page_texts)
            avg_conf: float | None = (
                sum(confidences) / len(confidences) if confidences else None
            )
            return raw_text, avg_conf, page_texts

        # Common shape 3: {"result": {"content": "...", "score": 0.95}}
        if "result" in data and isinstance(data["result"], dict):
            result = data["result"]
            raw_text = result.get("content", "")
            confidence = result.get("score")
            return raw_text, confidence, []

        # Fallback: stringify the entire response
        logger.warning("ocr.parse.unknown_response_shape", keys=list(data.keys()))
        return str(data), None, []


# ---------------------------------------------------------------------------
# FUTURE: Additional adapters
# ---------------------------------------------------------------------------

# class GoogleVisionAdapter(OCRAdapter):
#     """FUTURE: Google Cloud Vision OCR adapter."""
#     ENGINE_ID = "google_vision"
#     async def extract_text(self, image_path: str | Path) -> OCRResult:
#         raise NotImplementedError

# class AzureDocumentIntelligenceAdapter(OCRAdapter):
#     """FUTURE: Azure Document Intelligence (Form Recognizer) adapter."""
#     ENGINE_ID = "azure_di"
#     async def extract_text(self, image_path: str | Path) -> OCRResult:
#         raise NotImplementedError


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def get_ocr_adapter() -> OCRAdapter:
    """
    Return the configured OCR adapter instance.

    Currently always returns ``DeepSeekOCR2Adapter``.  In future iterations
    this can read ``settings.OCR_ENGINE`` to select the appropriate backend.
    """
    return DeepSeekOCR2Adapter()
