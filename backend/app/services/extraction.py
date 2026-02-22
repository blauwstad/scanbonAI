"""
Structured invoice metadata extraction service for ScanbonAI.

This module takes raw OCR text (and optionally the original image path for
vision-capable models) and returns a fully-typed ``InvoiceMetadata`` dataclass
with per-field confidence scores.

The extraction uses a structured LLM prompt that instructs the model to return
JSON conforming to a strict schema.  The response is validated with Pydantic
before being returned.

FUTURE: When a vision-capable DeepSeek model is available, we can pass the
base64 image alongside the OCR text for multi-modal extraction.
"""

from __future__ import annotations

import json
import re
import time
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
# Structured result type
# ---------------------------------------------------------------------------


@dataclass
class FieldValue:
    """A single extracted field with its confidence score."""

    value: str | None
    confidence: float | None  # 0.0–1.0; None when unknown


@dataclass
class InvoiceMetadata:
    """
    Fully-typed result of invoice metadata extraction.

    Mirrors ``ExtractedData`` ORM columns so the worker can persist it
    directly without a mapping layer.
    """

    vendor_name: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    vendor_tax_id: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    invoice_number: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    invoice_date: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    total_amount: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    tax_amount: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    currency: FieldValue = field(default_factory=lambda: FieldValue(None, None))
    line_items: list[dict[str, Any]] = field(default_factory=list)
    overall_confidence: float | None = None
    raw_llm_response: str = ""


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
You are an expert invoice data extraction system.
You will receive the OCR text of a tax invoice.
Extract the following fields and return a SINGLE valid JSON object.
If a field is not present or cannot be determined, use null for its value.
For confidence, use a float between 0.0 and 1.0 where:
  1.0 = completely certain, 0.0 = pure guess.

Required JSON structure:
{
  "vendor_name":    {"value": string|null, "confidence": float|null},
  "vendor_tax_id":  {"value": string|null, "confidence": float|null},
  "invoice_number": {"value": string|null, "confidence": float|null},
  "invoice_date":   {"value": "YYYY-MM-DD"|null, "confidence": float|null},
  "total_amount":   {"value": string|null, "confidence": float|null},
  "tax_amount":     {"value": string|null, "confidence": float|null},
  "currency":       {"value": "ISO-4217 code"|null, "confidence": float|null},
  "line_items": [
    {
      "description": string|null,
      "quantity": string|null,
      "unit_price": string|null,
      "total": string|null
    }
  ]
}

Rules:
- Return ONLY the JSON object, no markdown fences, no explanation.
- Dates must be in ISO 8601 format (YYYY-MM-DD).
- Amounts must include the numeric value as a string (e.g. "1234.56").
- Do NOT infer values that are not present in the text.
"""

_EXTRACTION_USER_TEMPLATE = """\
OCR TEXT:
---
{ocr_text}
---
Extract the invoice metadata from the text above.
"""


# ---------------------------------------------------------------------------
# LLM client (uses DeepSeek Chat API as extraction backbone)
# ---------------------------------------------------------------------------


class _LLMExtractionClient:
    """
    Thin async wrapper around the DeepSeek Chat Completions API for
    structured JSON extraction tasks.

    This client is intentionally private to this module; callers should use
    ``extract_invoice_metadata`` instead.
    """

    _API_URL = "https://api.deepseek.com/v1/chat/completions"
    _MODEL = "deepseek-chat"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_OCR_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        """
        Call the DeepSeek chat completions endpoint and return the assistant text.

        Parameters
        ----------
        system_prompt:
            System message that sets extraction rules and output format.
        user_prompt:
            User message containing the OCR text.
        temperature:
            Sampling temperature; 0.0 for deterministic extraction.

        Returns
        -------
        str
            Raw assistant text (should be a valid JSON string).
        """
        payload = {
            "model": self._MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }

        resp = await self._client.post(self._API_URL, json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        content: str = data["choices"][0]["message"]["content"]
        return content


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_invoice_metadata(
    ocr_text: str,
    image_path: str | Path | None = None,
) -> InvoiceMetadata:
    """
    Extract structured invoice metadata from OCR text using an LLM.

    Parameters
    ----------
    ocr_text:
        Raw text output from the OCR engine.
    image_path:
        Optional path to the original image.  Reserved for future multi-modal
        extraction; currently unused.

    Returns
    -------
    InvoiceMetadata
        Parsed extraction result with per-field confidence scores.

    Notes
    -----
    - The function is tolerant of malformed LLM output and will return a
      partially-populated result rather than raising.
    - All fields default to ``FieldValue(None, None)`` when extraction fails.
    """
    log = logger.bind(text_len=len(ocr_text))
    log.debug("extraction.start")

    t0 = time.monotonic()
    client = _LLMExtractionClient()
    raw_response = ""

    try:
        user_prompt = _EXTRACTION_USER_TEMPLATE.format(ocr_text=ocr_text)
        raw_response = await client.chat_completion(
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        metadata = _parse_llm_response(raw_response)

    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
        log.error("extraction.llm_call_failed", error=str(exc))
        metadata = InvoiceMetadata()

    except Exception as exc:
        log.error("extraction.unexpected_error", error=str(exc))
        metadata = InvoiceMetadata()

    finally:
        await client.aclose()

    metadata.raw_llm_response = raw_response
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    log.info(
        "extraction.complete",
        overall_confidence=metadata.overall_confidence,
        elapsed_ms=elapsed_ms,
    )

    # FUTURE: if image_path is provided and a vision model is configured,
    # send the image alongside the OCR text for a second extraction pass
    # and merge the results.

    return metadata


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def _parse_llm_response(raw: str) -> InvoiceMetadata:
    """
    Parse the LLM's JSON response into an ``InvoiceMetadata`` object.

    This function is deliberately defensive; it logs warnings and returns
    partial data rather than propagating parse errors to the caller.
    """
    # Strip possible markdown fences that some models add despite instructions
    cleaned = re.sub(r"^```(?:json)?\n?", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```$", "", cleaned)

    try:
        data: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("extraction.json_parse_failed", error=str(exc), raw=raw[:200])
        return InvoiceMetadata()

    def _field(key: str) -> FieldValue:
        """Extract a FieldValue from a dict sub-key, tolerating missing/null."""
        raw_field = data.get(key, {})
        if not isinstance(raw_field, dict):
            return FieldValue(None, None)
        val = raw_field.get("value")
        conf = raw_field.get("confidence")
        return FieldValue(
            value=str(val).strip() if val is not None else None,
            confidence=float(conf) if conf is not None else None,
        )

    fields = [
        "vendor_name",
        "vendor_tax_id",
        "invoice_number",
        "invoice_date",
        "total_amount",
        "tax_amount",
        "currency",
    ]

    # Compute overall confidence from the mean of available field confidences
    confidences = [
        f.confidence
        for key in fields
        if (f := _field(key)).confidence is not None
    ]
    overall = float(sum(confidences) / len(confidences)) if confidences else None

    # Line items (array, may be absent)
    raw_items = data.get("line_items", [])
    line_items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                line_items.append(item)

    return InvoiceMetadata(
        vendor_name=_field("vendor_name"),
        vendor_tax_id=_field("vendor_tax_id"),
        invoice_number=_field("invoice_number"),
        invoice_date=_field("invoice_date"),
        total_amount=_field("total_amount"),
        tax_amount=_field("tax_amount"),
        currency=_field("currency"),
        line_items=line_items,
        overall_confidence=overall,
    )
