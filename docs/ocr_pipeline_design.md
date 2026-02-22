# ScanbonAI -- OCR Pipeline, Dataset Strategy & Metrics

---

## DELIVERABLE 1: OCR Pipeline Design

### 1.1 Pipeline Architecture

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌─────────────┐
│ Image Intake │───▶│ Quality Gate │───▶│ Pre-Processing│───▶│  OCR Engine │
│ (file path)  │    │ (pass/fail)  │    │ (OpenCV)      │    │ (Adapter)   │
└─────────────┘    └──────┬───────┘    └───────────────┘    └──────┬──────┘
                          │ FAIL                                   │
                          ▼                                        ▼
                   ┌──────────────┐                   ┌────────────────────┐
                   │ Reject with  │                   │ Structured Extract │
                   │ feedback msg │                   │ (LLM / heuristic)  │
                   └──────────────┘                   └─────────┬──────────┘
                                                                │
                                                                ▼
                                                     ┌──────────────────┐
                                                     │ Confidence Score │
                                                     │ + Result Storage │
                                                     └──────────────────┘
```

**Stage-by-stage description:**

| # | Stage | Responsibility | Output |
|---|-------|---------------|--------|
| 1 | **Image Intake** | Load image bytes from stored file path, validate format (JPEG/PNG/HEIC/PDF page) | `RawImage` with metadata |
| 2 | **Quality Gate** | Blur, resolution, skew, exposure checks | `QualityReport` (pass/fail + per-check scores) |
| 3 | **Pre-Processing** | Deskew, contrast (CLAHE), noise reduction (bilateral filter), binarisation | Cleaned image bytes |
| 4 | **OCR Engine** | Extract raw text via adapter (DeepSeek OCR 2 primary) | `OCRResult` with raw text + word-level boxes |
| 5 | **Structured Extraction** | Parse raw text into invoice JSON via LLM prompt | `InvoiceData` (typed dataclass) |
| 6 | **Confidence Scoring** | Per-field confidence from model logprobs + heuristic validators | Scores attached to each field |
| 7 | **Result Storage** | Persist to DB, emit event for downstream (booking, user review) | Database row + domain event |

---

### 1.2 Core Data Models

```python
"""
scanbonai/ocr/models.py

Core data models for the OCR pipeline.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class QualityVerdict(str, Enum):
    """Overall quality gate outcome."""
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"  # processable but may degrade accuracy


@dataclass(frozen=True)
class QualityCheckResult:
    """Result of a single quality check."""
    name: str
    passed: bool
    score: float          # 0.0 – 1.0 normalised score
    threshold: float      # the threshold used
    detail: str = ""      # human-readable explanation


@dataclass(frozen=True)
class QualityReport:
    """Aggregated quality gate report."""
    verdict: QualityVerdict
    checks: list[QualityCheckResult]
    overall_score: float  # weighted average of individual scores

    @property
    def failed_checks(self) -> list[QualityCheckResult]:
        return [c for c in self.checks if not c.passed]


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned bounding box (pixel coordinates)."""
    x_min: int
    y_min: int
    x_max: int
    y_max: int


@dataclass(frozen=True)
class WordDetection:
    """A single word detected by OCR with its bounding box."""
    text: str
    bbox: BoundingBox
    confidence: float  # 0.0 – 1.0


@dataclass(frozen=True)
class OCRResult:
    """Raw output from the OCR adapter."""
    raw_text: str
    words: list[WordDetection]
    provider: str                    # e.g. "deepseek_ocr_2"
    model_version: str
    processing_time_ms: float
    metadata: dict = field(default_factory=dict)


@dataclass
class FieldConfidence:
    """Confidence information for a single extracted field."""
    value_raw: str               # the raw string as extracted
    confidence: float            # 0.0 – 1.0
    extraction_method: str       # "llm_logprob" | "regex" | "heuristic"
    validation_passed: bool      # did format/range validation pass?
    validation_detail: str = ""


@dataclass
class LineItem:
    """A single line item on an invoice."""
    description: str
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    total_price: Optional[Decimal] = None
    vat_rate: Optional[Decimal] = None
    confidence: Optional[FieldConfidence] = None


@dataclass
class InvoiceData:
    """Structured invoice data extracted from OCR text."""
    # -- identifiers --
    invoice_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None

    # -- supplier --
    supplier_name: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_kvk: Optional[str] = None          # Dutch Chamber of Commerce number
    supplier_vat_id: Optional[str] = None        # BTW-nummer
    supplier_iban: Optional[str] = None

    # -- amounts --
    subtotal: Optional[Decimal] = None
    vat_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    currency: str = "EUR"

    # -- line items --
    line_items: list[LineItem] = field(default_factory=list)

    # -- classification --
    category: Optional[str] = None               # e.g. "office_supplies", "travel"

    # -- confidence map (field_name -> FieldConfidence) --
    field_confidences: dict[str, FieldConfidence] = field(default_factory=dict)

    # -- metadata --
    ocr_provider: Optional[str] = None
    ocr_model_version: Optional[str] = None
    extracted_at: datetime = field(default_factory=datetime.utcnow)
    processing_time_ms: float = 0.0


@dataclass(frozen=True)
class PipelineResult:
    """Complete result of the OCR pipeline."""
    image_path: str
    quality_report: QualityReport
    ocr_result: Optional[OCRResult]
    invoice_data: Optional[InvoiceData]
    success: bool
    error_message: Optional[str] = None
```

---

### 1.3 Adapter Interface

```python
"""
scanbonai/ocr/adapters/base.py

Abstract OCR adapter interface.
All OCR providers (DeepSeek, Google Vision, Tesseract, etc.) implement this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from scanbonai.ocr.models import OCRResult


class OCRAdapter(ABC):
    """
    Abstract base class for OCR provider adapters.

    Every concrete adapter must implement:
      - extract_text: run OCR on a single image and return structured result.
      - health_check: verify the provider is reachable and operational.

    Adapters are designed to be stateless per-call; connection pooling
    and caching are handled at the infrastructure layer.
    """

    @abstractmethod
    async def extract_text(self, image_path: str | Path) -> OCRResult:
        """
        Run OCR on the image at *image_path* and return extracted text
        with word-level bounding boxes and confidence scores.

        Args:
            image_path: Absolute filesystem path to the image file.

        Returns:
            OCRResult containing raw text, word detections, and metadata.

        Raises:
            OCRProviderError: If the provider returns an unrecoverable error.
            FileNotFoundError: If the image file does not exist.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Verify that the OCR provider is healthy and accepting requests.

        Returns:
            True if the provider responded successfully, False otherwise.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (e.g. 'deepseek_ocr_2')."""
        ...


class OCRProviderError(Exception):
    """Raised when an OCR provider returns an unrecoverable error."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message} (status={status_code})")
```

---

### 1.4 DeepSeek OCR 2 Adapter (Primary)

```python
"""
scanbonai/ocr/adapters/deepseek_ocr2.py

Concrete adapter for DeepSeek-OCR 2, using the OpenAI-compatible
chat completions endpoint (works with DeepSeek API, vLLM, or Clarifai).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI

from scanbonai.ocr.adapters.base import OCRAdapter, OCRProviderError
from scanbonai.ocr.models import BoundingBox, OCRResult, WordDetection

logger = logging.getLogger(__name__)

# Default system prompt for structured OCR extraction
_SYSTEM_PROMPT = (
    "You are a precise document OCR engine. "
    "Extract ALL text from the provided image exactly as it appears. "
    "Preserve layout structure. Return only the extracted text, no commentary."
)

# Prompt requesting markdown-formatted OCR output for structured parsing
_OCR_USER_PROMPT = (
    "<image>\n<|grounding|>Convert the document to markdown. "
    "Preserve all numbers, dates, currency amounts, and special characters exactly."
)


class DeepSeekOCR2Config:
    """Configuration for DeepSeek OCR 2 adapter."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-ocr-2",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        base_size: int = 1024,
        image_size: int = 768,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.base_size = base_size
        self.image_size = image_size


class DeepSeekOCR2Adapter(OCRAdapter):
    """
    OCR adapter for DeepSeek-OCR 2.

    Uses the OpenAI-compatible chat completions API with vision support.
    The image is base64-encoded and sent as an image_url in the user message.
    """

    def __init__(self, config: DeepSeekOCR2Config) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    @property
    def provider_name(self) -> str:
        return "deepseek_ocr_2"

    async def extract_text(self, image_path: str | Path) -> OCRResult:
        """
        Send image to DeepSeek OCR 2 and parse the response.

        The image is base64-encoded and submitted via the chat completions
        endpoint. The model returns markdown-formatted text which is then
        parsed into an OCRResult.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        image_b64 = self._encode_image(path)
        mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"

        start_ms = time.monotonic() * 1000

        try:
            response = await self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_b64}",
                                },
                            },
                            {
                                "type": "text",
                                "text": _OCR_USER_PROMPT,
                            },
                        ],
                    },
                ],
                max_tokens=4096,
                temperature=0.0,  # deterministic extraction
            )
        except Exception as exc:
            raise OCRProviderError(
                provider=self.provider_name,
                message=str(exc),
            ) from exc

        elapsed_ms = time.monotonic() * 1000 - start_ms
        raw_text = response.choices[0].message.content or ""

        # Parse word-level detections from structured output if available
        words = self._parse_words(raw_text, response)

        return OCRResult(
            raw_text=raw_text,
            words=words,
            provider=self.provider_name,
            model_version=self._config.model,
            processing_time_ms=elapsed_ms,
            metadata={
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                },
                "finish_reason": response.choices[0].finish_reason,
            },
        )

    async def health_check(self) -> bool:
        """
        Verify the DeepSeek API is reachable by listing available models.
        """
        try:
            models = await self._client.models.list()
            return any(
                self._config.model in (m.id or "")
                for m in models.data
            )
        except Exception:
            logger.exception("DeepSeek OCR 2 health check failed")
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_image(path: Path) -> str:
        """Read and base64-encode an image file."""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def _parse_words(
        raw_text: str,
        response: Any,
    ) -> list[WordDetection]:
        """
        Parse word-level detections from the model output.

        DeepSeek OCR 2 with <|grounding|> mode can return bounding boxes
        in its output.  When not available, we fall back to splitting the
        raw text into tokens without spatial information.
        """
        words: list[WordDetection] = []

        # Attempt to parse grounding annotations (format: [[x1,y1,x2,y2]])
        # This is a simplified parser; production would use the model's
        # native grounding format.
        for token in raw_text.split():
            clean = token.strip()
            if clean:
                words.append(
                    WordDetection(
                        text=clean,
                        bbox=BoundingBox(x_min=0, y_min=0, x_max=0, y_max=0),
                        confidence=0.95,  # placeholder until logprobs available
                    )
                )

        return words
```

---

### 1.5 Fallback Provider Stubs

Adding a new provider requires only implementing `OCRAdapter`:

```python
"""
scanbonai/ocr/adapters/google_vision.py

Stub for Google Cloud Vision fallback adapter.
"""
from __future__ import annotations

from pathlib import Path

from scanbonai.ocr.adapters.base import OCRAdapter
from scanbonai.ocr.models import OCRResult


class GoogleVisionAdapter(OCRAdapter):
    """
    Google Cloud Vision OCR adapter (fallback).

    Requires: google-cloud-vision>=3.0
    """

    def __init__(self, credentials_path: str) -> None:
        self._credentials_path = credentials_path
        # from google.cloud import vision
        # self._client = vision.ImageAnnotatorClient.from_service_account_json(...)

    @property
    def provider_name(self) -> str:
        return "google_vision"

    async def extract_text(self, image_path: str | Path) -> OCRResult:
        raise NotImplementedError("Google Vision adapter not yet implemented")

    async def health_check(self) -> bool:
        raise NotImplementedError("Google Vision adapter not yet implemented")
```

```python
"""
scanbonai/ocr/adapters/tesseract.py

Stub for Tesseract fallback adapter (local, no API cost).
"""
from __future__ import annotations

from pathlib import Path

from scanbonai.ocr.adapters.base import OCRAdapter
from scanbonai.ocr.models import OCRResult


class TesseractAdapter(OCRAdapter):
    """
    Tesseract OCR adapter (offline fallback).

    Requires: pytesseract, tesseract-ocr system package.
    Useful for: local development, cost control, air-gapped deployments.
    """

    def __init__(self, lang: str = "nld+eng") -> None:
        self._lang = lang

    @property
    def provider_name(self) -> str:
        return "tesseract"

    async def extract_text(self, image_path: str | Path) -> OCRResult:
        raise NotImplementedError("Tesseract adapter not yet implemented")

    async def health_check(self) -> bool:
        raise NotImplementedError("Tesseract adapter not yet implemented")
```

---

### 1.6 Adapter Orchestrator with Fallback Chain

```python
"""
scanbonai/ocr/adapters/orchestrator.py

Manages primary + fallback OCR adapters with circuit-breaker logic.
"""
from __future__ import annotations

import logging
from pathlib import Path

from scanbonai.ocr.adapters.base import OCRAdapter, OCRProviderError
from scanbonai.ocr.models import OCRResult

logger = logging.getLogger(__name__)


class OCROrchestrator:
    """
    Tries the primary OCR adapter first, then falls through
    the fallback chain on failure.

    Usage:
        orchestrator = OCROrchestrator(
            primary=deepseek_adapter,
            fallbacks=[google_vision_adapter, tesseract_adapter],
        )
        result = await orchestrator.extract(image_path)
    """

    def __init__(
        self,
        primary: OCRAdapter,
        fallbacks: list[OCRAdapter] | None = None,
    ) -> None:
        self._primary = primary
        self._fallbacks = fallbacks or []

    async def extract(self, image_path: str | Path) -> OCRResult:
        """
        Attempt OCR extraction, falling back through the chain on failure.

        Raises:
            OCRProviderError: If all providers in the chain fail.
        """
        chain = [self._primary, *self._fallbacks]
        last_error: Exception | None = None

        for adapter in chain:
            try:
                logger.info(
                    "Attempting OCR with provider=%s for image=%s",
                    adapter.provider_name,
                    image_path,
                )
                result = await adapter.extract_text(image_path)
                logger.info(
                    "OCR succeeded with provider=%s (%.0f ms)",
                    adapter.provider_name,
                    result.processing_time_ms,
                )
                return result
            except (OCRProviderError, NotImplementedError) as exc:
                logger.warning(
                    "OCR provider %s failed: %s -- trying next fallback",
                    adapter.provider_name,
                    exc,
                )
                last_error = exc

        raise OCRProviderError(
            provider="orchestrator",
            message=f"All OCR providers failed. Last error: {last_error}",
        )
```

---

### 1.7 Quality Gates

```python
"""
scanbonai/ocr/quality.py

Pre-OCR image quality checks using OpenCV.

Each check returns a QualityCheckResult with:
  - A normalised score (0.0 = worst, 1.0 = best)
  - A pass/fail verdict against a configurable threshold
  - A human-readable detail message

The QualityGate aggregates all checks into a QualityReport.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

from scanbonai.ocr.models import QualityCheckResult, QualityReport, QualityVerdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class QualityGateConfig:
    """Thresholds for each quality check.  All scores are 0.0 – 1.0."""
    blur_threshold: float = 0.40
    resolution_min_width: int = 640
    resolution_min_height: int = 480
    resolution_threshold: float = 0.50
    skew_max_degrees: float = 15.0
    skew_threshold: float = 0.50
    exposure_threshold: float = 0.35
    overall_pass_threshold: float = 0.45   # weighted average must exceed this
    warn_threshold: float = 0.35           # below pass but above warn = WARN


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_blur(image: NDArray, threshold: float = 0.40) -> QualityCheckResult:
    """
    Detect blur using the variance of the Laplacian.

    A sharply focused image produces high variance; a blurry image
    produces low variance.  The score is normalised by mapping the
    Laplacian variance onto [0, 1] with a sigmoid-style curve.

    Args:
        image: Grayscale uint8 image array.
        threshold: Minimum acceptable normalised score.

    Returns:
        QualityCheckResult for the blur check.
    """
    gray = (
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(image.shape) == 3
        else image
    )
    laplacian_var: float = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # Map to [0, 1].  Empirically, variance < 50 is very blurry,
    # > 500 is very sharp.  We use a simple logistic mapping.
    score = min(1.0, laplacian_var / 500.0)
    passed = score >= threshold

    return QualityCheckResult(
        name="blur_detection",
        passed=passed,
        score=round(score, 4),
        threshold=threshold,
        detail=f"Laplacian variance={laplacian_var:.1f}, normalised={score:.4f}",
    )


def check_resolution(
    image: NDArray,
    min_width: int = 640,
    min_height: int = 480,
    threshold: float = 0.50,
) -> QualityCheckResult:
    """
    Verify minimum resolution (pixel dimensions).

    Score is the ratio of actual megapixels to the required minimum,
    capped at 1.0.

    Args:
        image: Image array (any colour space).
        min_width: Minimum acceptable width in pixels.
        min_height: Minimum acceptable height in pixels.
        threshold: Minimum acceptable normalised score.

    Returns:
        QualityCheckResult for the resolution check.
    """
    h, w = image.shape[:2]
    required_pixels = min_width * min_height
    actual_pixels = h * w

    score = min(1.0, actual_pixels / required_pixels) if required_pixels > 0 else 0.0
    passed = (w >= min_width and h >= min_height) and score >= threshold

    return QualityCheckResult(
        name="resolution_check",
        passed=passed,
        score=round(score, 4),
        threshold=threshold,
        detail=f"Image {w}x{h} (required >= {min_width}x{min_height})",
    )


def check_skew(
    image: NDArray,
    max_degrees: float = 15.0,
    threshold: float = 0.50,
) -> QualityCheckResult:
    """
    Estimate document skew angle using the Hough Line Transform.

    The dominant line angle is computed and compared against the
    maximum acceptable skew.

    Args:
        image: BGR or grayscale image array.
        max_degrees: Maximum acceptable skew in degrees.
        threshold: Minimum acceptable normalised score.

    Returns:
        QualityCheckResult for the skew check.
    """
    gray = (
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(image.shape) == 3
        else image
    )
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=gray.shape[1] // 4,
        maxLineGap=10,
    )

    if lines is None or len(lines) == 0:
        # Cannot determine skew; assume acceptable
        return QualityCheckResult(
            name="skew_detection",
            passed=True,
            score=0.80,
            threshold=threshold,
            detail="No lines detected; skew indeterminate (assumed OK)",
        )

    angles: list[float] = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle_deg = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        angles.append(angle_deg)

    median_angle = float(np.median(angles))
    # Normalise: 0 degrees skew -> score 1.0, max_degrees -> score 0.0
    skew_abs = min(abs(median_angle), max_degrees)
    score = max(0.0, 1.0 - (skew_abs / max_degrees))
    passed = score >= threshold

    return QualityCheckResult(
        name="skew_detection",
        passed=passed,
        score=round(score, 4),
        threshold=threshold,
        detail=f"Median skew angle={median_angle:.2f}°, normalised score={score:.4f}",
    )


def check_exposure(
    image: NDArray,
    threshold: float = 0.35,
) -> QualityCheckResult:
    """
    Analyse shadow and exposure using histogram analysis.

    Checks for:
      - Over-exposure: large spike at the bright end (>240).
      - Under-exposure: large spike at the dark end (<15).
      - Low contrast: narrow histogram spread.

    Score penalises both extremes and low contrast.

    Args:
        image: BGR or grayscale image array.
        threshold: Minimum acceptable normalised score.

    Returns:
        QualityCheckResult for the exposure/shadow check.
    """
    gray = (
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(image.shape) == 3
        else image
    )
    total_pixels = float(gray.shape[0] * gray.shape[1])
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()

    # Fraction of pixels at extremes
    dark_frac = float(hist[:15].sum()) / total_pixels
    bright_frac = float(hist[240:].sum()) / total_pixels

    # Contrast via standard deviation of intensities
    std_dev = float(np.std(gray))
    contrast_score = min(1.0, std_dev / 64.0)  # 64 is "good" std_dev

    # Penalise extremes
    exposure_penalty = max(dark_frac, bright_frac)
    score = max(0.0, contrast_score * (1.0 - exposure_penalty))
    passed = score >= threshold

    detail_parts = [
        f"dark_frac={dark_frac:.3f}",
        f"bright_frac={bright_frac:.3f}",
        f"std_dev={std_dev:.1f}",
        f"contrast_score={contrast_score:.3f}",
    ]

    return QualityCheckResult(
        name="exposure_analysis",
        passed=passed,
        score=round(score, 4),
        threshold=threshold,
        detail=", ".join(detail_parts),
    )


# ---------------------------------------------------------------------------
# Quality Gate (aggregator)
# ---------------------------------------------------------------------------

class QualityGate:
    """
    Run all quality checks and produce an aggregate QualityReport.

    Usage:
        gate = QualityGate(config=QualityGateConfig())
        report = gate.evaluate("/path/to/invoice.jpg")
    """

    # Weights for the weighted average (must sum to 1.0)
    _WEIGHTS: dict[str, float] = {
        "blur_detection": 0.35,
        "resolution_check": 0.20,
        "skew_detection": 0.20,
        "exposure_analysis": 0.25,
    }

    def __init__(self, config: QualityGateConfig | None = None) -> None:
        self._config = config or QualityGateConfig()

    def evaluate(self, image_path: str | Path) -> QualityReport:
        """
        Run all quality checks on the image at *image_path*.

        Args:
            image_path: Path to the image file.

        Returns:
            QualityReport with per-check results and overall verdict.

        Raises:
            FileNotFoundError: If the image does not exist.
            ValueError: If the image cannot be decoded.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Could not decode image: {path}")

        checks = [
            check_blur(image, threshold=self._config.blur_threshold),
            check_resolution(
                image,
                min_width=self._config.resolution_min_width,
                min_height=self._config.resolution_min_height,
                threshold=self._config.resolution_threshold,
            ),
            check_skew(
                image,
                max_degrees=self._config.skew_max_degrees,
                threshold=self._config.skew_threshold,
            ),
            check_exposure(image, threshold=self._config.exposure_threshold),
        ]

        # Weighted average
        overall_score = sum(
            self._WEIGHTS.get(c.name, 0.25) * c.score for c in checks
        )

        # Determine verdict
        if overall_score >= self._config.overall_pass_threshold and all(c.passed for c in checks):
            verdict = QualityVerdict.PASS
        elif overall_score >= self._config.warn_threshold:
            verdict = QualityVerdict.WARN
        else:
            verdict = QualityVerdict.FAIL

        report = QualityReport(
            verdict=verdict,
            checks=checks,
            overall_score=round(overall_score, 4),
        )

        logger.info(
            "Quality gate: verdict=%s, overall_score=%.4f, path=%s",
            verdict.value,
            overall_score,
            path,
        )
        return report
```

---

### 1.8 Pre-Processing Module

```python
"""
scanbonai/ocr/preprocessing.py

Image pre-processing pipeline: deskew, contrast enhancement, noise reduction.
Applied after the quality gate passes (or on WARN images to improve them).
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


class ImagePreprocessor:
    """
    Pre-process invoice images before OCR to maximise extraction accuracy.

    Pipeline order:
      1. Deskew (rotation correction)
      2. Contrast enhancement (CLAHE)
      3. Noise reduction (bilateral filter)
      4. Optional adaptive binarisation

    All operations return a new array; the input is never mutated.
    """

    def __init__(
        self,
        clahe_clip_limit: float = 2.0,
        clahe_grid_size: tuple[int, int] = (8, 8),
        bilateral_d: int = 9,
        bilateral_sigma_color: float = 75.0,
        bilateral_sigma_space: float = 75.0,
        enable_binarisation: bool = False,
    ) -> None:
        self._clahe_clip = clahe_clip_limit
        self._clahe_grid = clahe_grid_size
        self._bilateral_d = bilateral_d
        self._bilateral_sigma_color = bilateral_sigma_color
        self._bilateral_sigma_space = bilateral_sigma_space
        self._enable_binarisation = enable_binarisation

    def process(self, image: NDArray) -> NDArray:
        """
        Run the full pre-processing pipeline.

        Args:
            image: BGR image as a NumPy array.

        Returns:
            Pre-processed BGR image.
        """
        result = self.deskew(image)
        result = self.enhance_contrast(result)
        result = self.reduce_noise(result)
        if self._enable_binarisation:
            result = self.binarise(result)
        return result

    def deskew(self, image: NDArray) -> NDArray:
        """
        Correct document skew by detecting the dominant text angle
        and rotating the image to align it.

        Uses the Hough Line Transform to find the median line angle,
        then applies an affine rotation.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=100,
            minLineLength=gray.shape[1] // 4,
            maxLineGap=10,
        )

        if lines is None or len(lines) == 0:
            logger.debug("No lines detected; skipping deskew")
            return image

        angles = [
            float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            for line in lines
            for x1, y1, x2, y2 in [line[0]]
        ]
        median_angle = float(np.median(angles))

        if abs(median_angle) < 0.5:
            logger.debug("Skew angle %.2f° below threshold; skipping rotation", median_angle)
            return image

        h, w = image.shape[:2]
        centre = (w // 2, h // 2)
        rotation_matrix = cv2.getRotationMatrix2D(centre, median_angle, 1.0)
        rotated = cv2.warpAffine(
            image,
            rotation_matrix,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        logger.info("Deskewed image by %.2f°", median_angle)
        return rotated

    def enhance_contrast(self, image: NDArray) -> NDArray:
        """
        Apply CLAHE (Contrast Limited Adaptive Histogram Equalisation)
        to the L channel of the LAB colour space.

        This improves local contrast without over-amplifying noise.
        """
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)

        clahe = cv2.createCLAHE(
            clipLimit=self._clahe_clip,
            tileGridSize=self._clahe_grid,
        )
        l_enhanced = clahe.apply(l_channel)

        lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
        return cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

    def reduce_noise(self, image: NDArray) -> NDArray:
        """
        Apply bilateral filtering to reduce noise while preserving edges.

        Bilateral filtering is preferred over Gaussian blur because it
        keeps text edges sharp.
        """
        return cv2.bilateralFilter(
            image,
            d=self._bilateral_d,
            sigmaColor=self._bilateral_sigma_color,
            sigmaSpace=self._bilateral_sigma_space,
        )

    @staticmethod
    def binarise(image: NDArray) -> NDArray:
        """
        Apply adaptive Gaussian thresholding for binarisation.

        Useful for very noisy or low-contrast documents, but may hurt
        performance on colour invoices. Disabled by default.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        binary = cv2.adaptiveThreshold(
            gray,
            maxValue=255,
            adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            thresholdType=cv2.THRESH_BINARY,
            blockSize=11,
            C=2,
        )
        # Convert back to 3-channel for consistency
        return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
```

---

### 1.9 Structured Extraction (LLM-Based)

```python
"""
scanbonai/ocr/extraction.py

Structured extraction: raw OCR text -> typed InvoiceData JSON.

Uses an LLM (via OpenAI-compatible API) with a carefully engineered
prompt template to extract invoice fields.  Falls back to regex-based
heuristics for critical fields when the LLM confidence is low.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from openai import AsyncOpenAI

from scanbonai.ocr.models import FieldConfidence, InvoiceData, LineItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extraction prompt template
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You are a Dutch tax invoice data extraction engine. You extract structured
data from OCR text of invoices and receipts used for Dutch tax administration.

RULES:
- Extract ONLY information present in the text. Never fabricate data.
- For amounts: use dot as decimal separator, no thousands separator. Example: 1234.56
- For dates: use ISO 8601 format (YYYY-MM-DD).
- For VAT IDs: Dutch format is NL + 9 digits + B + 2 digits (e.g. NL123456789B01).
- For KVK numbers: 8 digits.
- For IBAN: NL + 2 check digits + 4 letters + 10 digits.
- If a field is not found in the text, set it to null.
- For each field, provide a confidence score between 0.0 and 1.0.
- Classify the expense into one of these categories:
  office_supplies, travel, meals_entertainment, software_subscriptions,
  professional_services, utilities, insurance, vehicle, marketing,
  equipment, rent, telecommunications, other

Respond with valid JSON only. No markdown, no commentary.
"""

EXTRACTION_USER_TEMPLATE = """\
Extract structured invoice data from the following OCR text.

OCR TEXT:
---
{ocr_text}
---

Return JSON with this exact schema:
{{
  "invoice_number": string | null,
  "invoice_date": "YYYY-MM-DD" | null,
  "due_date": "YYYY-MM-DD" | null,
  "supplier_name": string | null,
  "supplier_address": string | null,
  "supplier_kvk": string | null,
  "supplier_vat_id": string | null,
  "supplier_iban": string | null,
  "subtotal": number | null,
  "vat_amount": number | null,
  "total_amount": number | null,
  "currency": string,
  "category": string | null,
  "line_items": [
    {{
      "description": string,
      "quantity": number | null,
      "unit_price": number | null,
      "total_price": number | null,
      "vat_rate": number | null
    }}
  ],
  "field_confidences": {{
    "<field_name>": number
  }}
}}
"""


# ---------------------------------------------------------------------------
# Regex-based heuristic validators (for cross-checking LLM output)
# ---------------------------------------------------------------------------

# Dutch IBAN: NLdd ABCD 0123456789
_IBAN_PATTERN = re.compile(r"\bNL\d{2}\s?[A-Z]{4}\s?\d{10}\b")

# Dutch VAT ID: NL + 9 digits + B + 2 digits
_VAT_ID_PATTERN = re.compile(r"\bNL\d{9}B\d{2}\b")

# KVK: 8 digits
_KVK_PATTERN = re.compile(r"\b\d{8}\b")

# Date patterns: DD-MM-YYYY or DD/MM/YYYY (common in Dutch invoices)
_DATE_PATTERN_NL = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")

# Amount pattern: optional currency, digits with optional thousands sep and decimal
_AMOUNT_PATTERN = re.compile(
    r"[€$]?\s*(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})\b"
)


class InvoiceExtractor:
    """
    Extract structured InvoiceData from raw OCR text.

    Primary method: LLM-based extraction with a structured prompt.
    Secondary: regex-based heuristic validation and fallback.
    """

    def __init__(
        self,
        llm_client: AsyncOpenAI,
        model: str = "deepseek-chat",
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> None:
        self._client = llm_client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def extract(self, ocr_text: str) -> InvoiceData:
        """
        Extract structured invoice data from raw OCR text.

        Steps:
          1. Call LLM with structured extraction prompt.
          2. Parse JSON response.
          3. Cross-validate with regex heuristics.
          4. Build InvoiceData with per-field confidence.

        Args:
            ocr_text: Raw text output from OCR.

        Returns:
            Populated InvoiceData dataclass.
        """
        raw_json = await self._llm_extract(ocr_text)
        parsed = self._parse_llm_response(raw_json)
        invoice = self._build_invoice(parsed, ocr_text)
        return invoice

    async def _llm_extract(self, ocr_text: str) -> str:
        """Call the LLM and return the raw JSON string response."""
        user_message = EXTRACTION_USER_TEMPLATE.format(ocr_text=ocr_text)

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format={"type": "json_object"},
        )

        return response.choices[0].message.content or "{}"

    @staticmethod
    def _parse_llm_response(raw_json: str) -> dict[str, Any]:
        """Parse the LLM JSON response, handling common edge cases."""
        # Strip markdown code fences if present
        cleaned = raw_json.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM extraction response as JSON")
            return {}

    def _build_invoice(
        self,
        parsed: dict[str, Any],
        ocr_text: str,
    ) -> InvoiceData:
        """
        Build an InvoiceData from parsed LLM output, cross-validating
        with regex heuristics and computing confidence scores.
        """
        llm_confidences: dict[str, float] = parsed.get("field_confidences", {})
        field_confidences: dict[str, FieldConfidence] = {}

        def _extract_field(
            field_name: str,
            value: Any,
            regex_pattern: re.Pattern | None = None,
            validator: Any = None,
        ) -> tuple[Any, FieldConfidence]:
            """Extract a field with confidence scoring."""
            llm_conf = llm_confidences.get(field_name, 0.5)
            regex_found = False

            if regex_pattern and ocr_text:
                match = regex_pattern.search(ocr_text)
                regex_found = match is not None

            # Boost confidence if regex also found the value
            if regex_found and value is not None:
                final_conf = min(1.0, llm_conf * 1.2)
                method = "llm_logprob+regex"
                validation_passed = True
            elif value is not None:
                final_conf = llm_conf
                method = "llm_logprob"
                validation_passed = validator(value) if validator else True
            else:
                final_conf = 0.0
                method = "not_found"
                validation_passed = False

            confidence = FieldConfidence(
                value_raw=str(value) if value is not None else "",
                confidence=round(final_conf, 4),
                extraction_method=method,
                validation_passed=validation_passed,
            )
            return value, confidence

        # -- Extract each field --
        invoice_number, inv_num_conf = _extract_field(
            "invoice_number", parsed.get("invoice_number"),
        )

        invoice_date_str = parsed.get("invoice_date")
        invoice_date = self._parse_date(invoice_date_str)
        _, inv_date_conf = _extract_field("invoice_date", invoice_date_str)

        due_date_str = parsed.get("due_date")
        due_date = self._parse_date(due_date_str)
        _, due_date_conf = _extract_field("due_date", due_date_str)

        supplier_name, supplier_name_conf = _extract_field(
            "supplier_name", parsed.get("supplier_name"),
        )
        supplier_address, supplier_addr_conf = _extract_field(
            "supplier_address", parsed.get("supplier_address"),
        )
        supplier_kvk, kvk_conf = _extract_field(
            "supplier_kvk", parsed.get("supplier_kvk"), _KVK_PATTERN,
        )
        supplier_vat_id, vat_conf = _extract_field(
            "supplier_vat_id", parsed.get("supplier_vat_id"), _VAT_ID_PATTERN,
        )
        supplier_iban, iban_conf = _extract_field(
            "supplier_iban", parsed.get("supplier_iban"), _IBAN_PATTERN,
        )

        subtotal, subtotal_conf = _extract_field(
            "subtotal", self._parse_decimal(parsed.get("subtotal")),
        )
        vat_amount, vat_amt_conf = _extract_field(
            "vat_amount", self._parse_decimal(parsed.get("vat_amount")),
        )
        total_amount, total_conf = _extract_field(
            "total_amount", self._parse_decimal(parsed.get("total_amount")),
        )

        # Cross-validate: subtotal + vat should equal total
        if subtotal and vat_amount and total_amount:
            expected_total = subtotal + vat_amount
            if abs(expected_total - total_amount) < Decimal("0.02"):
                total_conf.validation_passed = True
                total_conf = FieldConfidence(
                    value_raw=total_conf.value_raw,
                    confidence=min(1.0, total_conf.confidence * 1.1),
                    extraction_method=total_conf.extraction_method,
                    validation_passed=True,
                    validation_detail="Cross-validated: subtotal + VAT = total",
                )

        category, cat_conf = _extract_field(
            "category", parsed.get("category"),
        )

        # -- Line items --
        line_items: list[LineItem] = []
        for item in parsed.get("line_items", []):
            if isinstance(item, dict):
                line_items.append(
                    LineItem(
                        description=item.get("description", ""),
                        quantity=self._parse_decimal(item.get("quantity")),
                        unit_price=self._parse_decimal(item.get("unit_price")),
                        total_price=self._parse_decimal(item.get("total_price")),
                        vat_rate=self._parse_decimal(item.get("vat_rate")),
                    )
                )

        # -- Assemble field confidences --
        field_confidences = {
            "invoice_number": inv_num_conf,
            "invoice_date": inv_date_conf,
            "due_date": due_date_conf,
            "supplier_name": supplier_name_conf,
            "supplier_address": supplier_addr_conf,
            "supplier_kvk": kvk_conf,
            "supplier_vat_id": vat_conf,
            "supplier_iban": iban_conf,
            "subtotal": subtotal_conf,
            "vat_amount": vat_amt_conf,
            "total_amount": total_conf,
            "category": cat_conf,
        }

        return InvoiceData(
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            supplier_name=supplier_name,
            supplier_address=supplier_address,
            supplier_kvk=supplier_kvk,
            supplier_vat_id=supplier_vat_id,
            supplier_iban=supplier_iban,
            subtotal=subtotal,
            vat_amount=vat_amount,
            total_amount=total_amount,
            currency=parsed.get("currency", "EUR"),
            line_items=line_items,
            category=category,
            field_confidences=field_confidences,
        )

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        """Parse an ISO 8601 date string, returning None on failure."""
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_decimal(value: Any) -> Optional[Decimal]:
        """Parse a numeric value to Decimal, returning None on failure."""
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
```

---

### 1.10 Pipeline Orchestrator

```python
"""
scanbonai/ocr/pipeline.py

Top-level OCR pipeline orchestrator.
Ties together: quality gate -> pre-processing -> OCR -> extraction -> storage.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2

from scanbonai.ocr.adapters.orchestrator import OCROrchestrator
from scanbonai.ocr.extraction import InvoiceExtractor
from scanbonai.ocr.models import (
    PipelineResult,
    QualityVerdict,
)
from scanbonai.ocr.preprocessing import ImagePreprocessor
from scanbonai.ocr.quality import QualityGate

logger = logging.getLogger(__name__)


class OCRPipeline:
    """
    End-to-end OCR pipeline for invoice processing.

    Usage:
        pipeline = OCRPipeline(
            quality_gate=QualityGate(),
            preprocessor=ImagePreprocessor(),
            ocr=OCROrchestrator(primary=deepseek),
            extractor=InvoiceExtractor(llm_client=client),
        )
        result = await pipeline.run("/uploads/tenant_123/invoice_456.jpg")
    """

    def __init__(
        self,
        quality_gate: QualityGate,
        preprocessor: ImagePreprocessor,
        ocr: OCROrchestrator,
        extractor: InvoiceExtractor,
    ) -> None:
        self._quality_gate = quality_gate
        self._preprocessor = preprocessor
        self._ocr = ocr
        self._extractor = extractor

    async def run(self, image_path: str | Path) -> PipelineResult:
        """
        Execute the full pipeline on a single image.

        Returns a PipelineResult regardless of success or failure,
        so callers can always inspect the quality report and error info.
        """
        path = Path(image_path)
        start = time.monotonic()

        # ---- Stage 1: Quality Gate ----
        try:
            quality_report = self._quality_gate.evaluate(path)
        except (FileNotFoundError, ValueError) as exc:
            return PipelineResult(
                image_path=str(path),
                quality_report=None,  # type: ignore[arg-type]
                ocr_result=None,
                invoice_data=None,
                success=False,
                error_message=f"Quality gate error: {exc}",
            )

        if quality_report.verdict == QualityVerdict.FAIL:
            logger.warning("Quality gate FAILED for %s", path)
            return PipelineResult(
                image_path=str(path),
                quality_report=quality_report,
                ocr_result=None,
                invoice_data=None,
                success=False,
                error_message=(
                    f"Image quality too low: "
                    f"{[c.name for c in quality_report.failed_checks]}"
                ),
            )

        # ---- Stage 2: Pre-Processing ----
        image = cv2.imread(str(path))
        processed_image = self._preprocessor.process(image)

        # Save processed image to a temp path for the OCR adapter
        processed_path = path.parent / f".processed_{path.name}"
        cv2.imwrite(str(processed_path), processed_image)

        # ---- Stage 3: OCR ----
        try:
            ocr_result = await self._ocr.extract(str(processed_path))
        except Exception as exc:
            logger.exception("OCR failed for %s", path)
            return PipelineResult(
                image_path=str(path),
                quality_report=quality_report,
                ocr_result=None,
                invoice_data=None,
                success=False,
                error_message=f"OCR error: {exc}",
            )

        # ---- Stage 4: Structured Extraction ----
        try:
            invoice_data = await self._extractor.extract(ocr_result.raw_text)
            invoice_data.ocr_provider = ocr_result.provider
            invoice_data.ocr_model_version = ocr_result.model_version
            invoice_data.processing_time_ms = (time.monotonic() - start) * 1000
        except Exception as exc:
            logger.exception("Extraction failed for %s", path)
            return PipelineResult(
                image_path=str(path),
                quality_report=quality_report,
                ocr_result=ocr_result,
                invoice_data=None,
                success=False,
                error_message=f"Extraction error: {exc}",
            )

        # ---- Stage 5: Cleanup ----
        try:
            processed_path.unlink(missing_ok=True)
        except OSError:
            pass

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Pipeline complete for %s in %.0f ms (provider=%s)",
            path,
            elapsed_ms,
            ocr_result.provider,
        )

        return PipelineResult(
            image_path=str(path),
            quality_report=quality_report,
            ocr_result=ocr_result,
            invoice_data=invoice_data,
            success=True,
        )
```

---

## DELIVERABLE 2: Dataset Strategy

### 2.1 Public Invoice Datasets

| Dataset | Source | Size | Content | Privacy | Relevance to ScanbonAI | Suggested Use |
|---------|--------|------|---------|---------|----------------------|---------------|
| **SROIE** (ICDAR 2019) | [Competition site](https://rrc.cvc.uab.es/?ch=13) / [Kaggle](https://www.kaggle.com/datasets/urbikn/sroie-datasetv2) | 1,000 images (600 train / 400 test) | Scanned receipts with text localisation, OCR, and key information extraction annotations | Public, research-only licence | **High** -- receipt layout close to simple invoices; pre-annotated with company, date, address, total | **Evaluation benchmark** for OCR accuracy and field extraction. Too small for training. |
| **CORD** | [GitHub (clovaai/cord)](https://github.com/clovaai/cord) / [OpenReview](https://openreview.net/pdf?id=SJl3z659UH) | 11,000+ images | Indonesian receipts with box-level text and 42 sub-class entity labels across 5 superclasses | Public, CC-BY | **Medium-High** -- richest public receipt dataset with fine-grained entity annotations; not Dutch but layout patterns transfer | **Evaluation + extraction prompt tuning**. Use for validating structured extraction accuracy. |
| **RVL-CDIP** | [Hugging Face (aharley/rvl_cdip)](https://huggingface.co/datasets/aharley/rvl_cdip) / [Original site](https://adamharley.com/rvl-cdip/) | 400,000 images (16 classes, 25K each) | Document classification: letter, form, invoice, budget, memo, etc. (grayscale) | Public (from Legacy Tobacco Document Library) | **Medium** -- "invoice" class useful for document type classification; images are old/grayscale US documents | **Document classification pre-training**. Filter to "invoice" and "budget" classes for pipeline smoke-testing. |
| **Invoices & Receipts OCR v1** | [Hugging Face (mychen76)](https://huggingface.co/datasets/mychen76/invoices-and-receipts_ocr_v1) | ~15,900 images | Invoices and receipts in 5 languages including Dutch | Public | **High** -- includes Dutch invoices directly relevant to our use case | **Evaluation + extraction prompt tuning** for Dutch-specific patterns. |
| **High-Quality Invoice Images** | [Kaggle](https://www.kaggle.com/datasets/osamahosamabdellatif/high-quality-invoice-images-for-ocr) | Varies | Clean invoice images optimised for OCR training | Public | **Medium** -- useful for quality gate calibration and pre-processing validation | **Quality gate threshold calibration** and pre-processing pipeline tuning. |

**Recommendation:** Start with SROIE for baseline OCR accuracy metrics, CORD for structured extraction evaluation, and the Invoices & Receipts OCR v1 dataset for Dutch-specific testing. RVL-CDIP is useful for document classification if we add a triage step.

---

### 2.2 Privacy-First Learning Pipeline

```python
"""
scanbonai/ocr/feedback.py

Privacy-first feedback and learning pipeline.

Stores correction data for continuous improvement while maintaining
strict tenant isolation and PII protection.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CorrectionSource(str, Enum):
    """Who made the correction."""
    USER_EDIT = "user_edit"
    ADMIN_OVERRIDE = "admin_override"
    EXPERT_REVIEW = "expert_review"


# Label quality tier weights
CORRECTION_WEIGHTS: dict[CorrectionSource, float] = {
    CorrectionSource.USER_EDIT: 1.0,
    CorrectionSource.ADMIN_OVERRIDE: 3.0,
    CorrectionSource.EXPERT_REVIEW: 5.0,  # base; scaled up to 10.0 by expert reputation
}


@dataclass
class FieldCorrection:
    """A correction to a single extracted field."""
    field_name: str
    original_value: Optional[str]
    corrected_value: Optional[str]
    confidence_original: float
    source: CorrectionSource
    weight: float  # effective weight considering source + expert reputation


@dataclass
class CorrectionRecord:
    """
    Complete correction record for a single invoice processing run.

    This is the unit of data stored for feedback learning.
    Raw PII is NEVER stored in this record -- only anonymised references.
    """
    # -- References (no raw PII) --
    record_id: str
    image_ref: str                          # hash-based reference, not file path
    tenant_id: str                          # for tenant isolation in queries
    model_version: str                      # OCR model version that produced the original

    # -- Correction data --
    field_corrections: list[FieldCorrection]
    correction_source: CorrectionSource
    corrected_at: datetime = field(default_factory=datetime.utcnow)

    # -- Anonymised extracted data (for prompt tuning) --
    ocr_text_hash: str = ""                 # SHA-256 of raw OCR text (for dedup)
    extracted_json_anonymised: dict = field(default_factory=dict)
    corrected_json_anonymised: dict = field(default_factory=dict)

    @property
    def has_corrections(self) -> bool:
        return len(self.field_corrections) > 0

    @property
    def correction_count(self) -> int:
        return len(self.field_corrections)


class FeedbackStore:
    """
    Manages correction records for the feedback learning pipeline.

    Responsibilities:
      1. Store correction records with tenant isolation.
      2. Anonymise PII before storage.
      3. Aggregate corrections across tenants for model improvement.
      4. Version datasets for reproducible fine-tuning.
    """

    def __init__(self, db_session: Any) -> None:
        """
        Args:
            db_session: Async database session (SQLAlchemy async or similar).
        """
        self._db = db_session

    async def store_correction(
        self,
        tenant_id: str,
        image_path: str,
        ocr_text: str,
        extracted_json: dict,
        corrected_json: dict,
        source: CorrectionSource,
        model_version: str,
        expert_reputation: float = 1.0,
    ) -> CorrectionRecord:
        """
        Store a correction record with PII anonymisation.

        Args:
            tenant_id: Tenant identifier for data isolation.
            image_path: Original image path (hashed, not stored raw).
            ocr_text: Raw OCR text (hashed for dedup, not stored raw).
            extracted_json: Original extraction output.
            corrected_json: User/admin/expert corrected output.
            source: Who made the correction.
            model_version: OCR model version string.
            expert_reputation: For expert reviews, a multiplier (1.0 - 2.0).

        Returns:
            The stored CorrectionRecord.
        """
        # Compute diffs
        field_corrections = self._compute_diffs(
            extracted_json, corrected_json, source, expert_reputation,
        )

        # Anonymise: strip PII fields, keep only structure + category info
        extracted_anon = self._anonymise(extracted_json)
        corrected_anon = self._anonymise(corrected_json)

        record = CorrectionRecord(
            record_id=self._generate_id(tenant_id, image_path),
            image_ref=hashlib.sha256(image_path.encode()).hexdigest()[:16],
            tenant_id=tenant_id,
            model_version=model_version,
            field_corrections=field_corrections,
            correction_source=source,
            ocr_text_hash=hashlib.sha256(ocr_text.encode()).hexdigest(),
            extracted_json_anonymised=extracted_anon,
            corrected_json_anonymised=corrected_anon,
        )

        # Persist (implementation depends on DB layer)
        # await self._db.insert(record)
        logger.info(
            "Stored correction record=%s tenant=%s corrections=%d source=%s",
            record.record_id,
            tenant_id,
            record.correction_count,
            source.value,
        )
        return record

    async def aggregate_corrections(
        self,
        field_name: str,
        min_count: int = 10,
    ) -> dict[str, Any]:
        """
        Aggregate corrections across all tenants for a specific field.

        Returns anonymised statistics:
          - total_corrections: int
          - error_rate: float (fraction of extractions that were corrected)
          - common_error_patterns: list of (original_pattern, corrected_pattern, count)
          - weighted_accuracy: float (weighted by correction source quality)

        This data is safe to use for prompt tuning and model evaluation.

        Args:
            field_name: The invoice field to aggregate (e.g. "total_amount").
            min_count: Minimum correction count to return results (k-anonymity).

        Returns:
            Aggregation statistics dict.
        """
        # Implementation would query the DB and compute statistics.
        # Placeholder structure:
        return {
            "field_name": field_name,
            "total_corrections": 0,
            "error_rate": 0.0,
            "common_error_patterns": [],
            "weighted_accuracy": 0.0,
        }

    @staticmethod
    def _compute_diffs(
        extracted: dict,
        corrected: dict,
        source: CorrectionSource,
        expert_reputation: float,
    ) -> list[FieldCorrection]:
        """Compute per-field diffs between extracted and corrected JSON."""
        corrections: list[FieldCorrection] = []
        base_weight = CORRECTION_WEIGHTS[source]

        # For expert reviews, scale weight by reputation (1.0x to 2.0x)
        effective_weight = base_weight * min(2.0, max(1.0, expert_reputation))

        all_keys = set(extracted.keys()) | set(corrected.keys())

        for key in all_keys:
            orig = extracted.get(key)
            corr = corrected.get(key)

            # Skip nested structures (line_items handled separately)
            if isinstance(orig, (list, dict)) or isinstance(corr, (list, dict)):
                continue

            if str(orig) != str(corr):
                corrections.append(
                    FieldCorrection(
                        field_name=key,
                        original_value=str(orig) if orig is not None else None,
                        corrected_value=str(corr) if corr is not None else None,
                        confidence_original=0.0,  # populated from extraction metadata
                        source=source,
                        weight=effective_weight,
                    )
                )

        return corrections

    @staticmethod
    def _anonymise(data: dict) -> dict:
        """
        Remove PII fields from invoice data for safe storage in the
        feedback learning pipeline.

        Retained: amounts, dates, category, field structure.
        Removed: supplier name, address, IBAN, KVK, VAT ID.
        """
        pii_fields = {
            "supplier_name",
            "supplier_address",
            "supplier_iban",
            "supplier_kvk",
            "supplier_vat_id",
        }
        return {
            k: ("[REDACTED]" if k in pii_fields else v)
            for k, v in data.items()
        }

    @staticmethod
    def _generate_id(tenant_id: str, image_path: str) -> str:
        """Generate a deterministic record ID from tenant + image."""
        raw = f"{tenant_id}:{image_path}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]
```

### 2.3 Dataset Versioning Strategy

```
Feedback Learning Pipeline (High-Level Flow)
=============================================

  User corrects invoice in WhatsApp
            │
            ▼
  ┌──────────────────────┐
  │ store_correction()   │  Anonymised, tenant-isolated
  │ -> CorrectionRecord  │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │ Nightly aggregation  │  Cross-tenant, k-anonymity (min_count=10)
  │ -> AggregatedStats   │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │ DVC dataset version  │  dvc add data/corrections/v{N}/
  │ + git tag            │  git tag dataset-v{N}
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────────────┐
  │ Phase 1: Prompt tuning       │  Adjust extraction prompt based on
  │ (no model weights changed)   │  common error patterns
  ├──────────────────────────────┤
  │ Phase 2: LoRA fine-tuning    │  Fine-tune extraction LLM on
  │ (future, when N > 5000)      │  anonymised correction pairs
  └──────────────────────────────┘

Privacy guardrails at each stage:
  - Raw images: NEVER leave tenant storage boundary
  - OCR text: hashed for dedup, NEVER stored in training sets
  - Correction records: PII fields redacted before aggregation
  - Aggregation: k-anonymity threshold (min 10 corrections per pattern)
  - Fine-tuning: differential privacy (DP-SGD) when model training begins
```

### 2.4 Expert Labels as Gold Data (FUTURE)

```python
"""
scanbonai/ocr/expert_labels.py

Expert labelling system for gold-standard training data (FUTURE).

Design for when expert reviewers (accountants, tax advisors) validate
invoice extractions to create high-quality training signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LabelQualityTier(str, Enum):
    """
    Quality tiers for correction labels, determining their weight
    in training and evaluation.
    """
    USER_EDIT = "user_edit"           # Weight: 1.0 -- end-user correction
    ADMIN_OVERRIDE = "admin_override" # Weight: 3.0 -- tenant admin review
    EXPERT_REVIEW = "expert_review"   # Weight: 5.0 - 10.0 -- professional reviewer


# Tier weight ranges
TIER_WEIGHT_RANGES: dict[LabelQualityTier, tuple[float, float]] = {
    LabelQualityTier.USER_EDIT: (1.0, 1.0),
    LabelQualityTier.ADMIN_OVERRIDE: (3.0, 3.0),
    LabelQualityTier.EXPERT_REVIEW: (5.0, 10.0),  # scaled by expert reputation score
}


@dataclass
class ExpertProfile:
    """
    Profile for an expert reviewer.

    reputation_score determines the weight multiplier within the
    EXPERT_REVIEW tier range (5.0 - 10.0).

    Reputation is computed from:
      - Agreement rate with other experts (inter-annotator agreement)
      - Historical accuracy (when ground truth is later confirmed)
      - Domain expertise level (e.g. registered accountant vs bookkeeper)
    """
    expert_id: str
    name: str
    domain: str                     # e.g. "dutch_tax", "eu_vat", "general_accounting"
    reputation_score: float         # 0.0 - 1.0, maps to weight 5.0 - 10.0
    total_reviews: int = 0
    agreement_rate: float = 0.0     # fraction agreeing with consensus

    @property
    def effective_weight(self) -> float:
        """Compute effective weight based on reputation within tier range."""
        min_w, max_w = TIER_WEIGHT_RANGES[LabelQualityTier.EXPERT_REVIEW]
        return min_w + (max_w - min_w) * self.reputation_score


@dataclass
class DriftDetectionConfig:
    """
    Configuration for accuracy drift detection.

    Monitors field-level accuracy over time and triggers alerts
    when performance degrades beyond thresholds.
    """
    # Window sizes for comparison
    baseline_window_days: int = 30       # "normal" performance window
    detection_window_days: int = 7       # recent performance window

    # Alert thresholds (relative drop from baseline)
    warning_threshold: float = 0.02      # 2% accuracy drop -> warning
    critical_threshold: float = 0.05     # 5% accuracy drop -> alert + rollback

    # Fields to monitor
    monitored_fields: list[str] = None   # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.monitored_fields is None:
            self.monitored_fields = [
                "invoice_number",
                "invoice_date",
                "total_amount",
                "vat_amount",
                "supplier_name",
                "supplier_vat_id",
                "supplier_iban",
            ]


# Target metrics and milestones
ACCURACY_MILESTONES = {
    "mvp": {
        "description": "MVP launch (WhatsApp intake working)",
        "target_field_accuracy": 0.90,       # 90% fields correct without edits
        "target_correction_rate": 0.40,      # 40% of invoices need some edit
    },
    "v1_stable": {
        "description": "V1 stable (prompt-tuned on 500+ corrections)",
        "target_field_accuracy": 0.95,
        "target_correction_rate": 0.20,
    },
    "v2_finetuned": {
        "description": "V2 fine-tuned (LoRA on 5000+ corrections)",
        "target_field_accuracy": 0.98,
        "target_correction_rate": 0.08,
    },
    "aspirational": {
        "description": "Long-term aspiration",
        "target_field_accuracy": 0.9999,     # 99.99% -- requires expert gold data
        "target_correction_rate": 0.01,
    },
}
```

---

## DELIVERABLE 3: Metrics & Monitoring

```python
"""
scanbonai/ocr/metrics.py

Metrics collection and monitoring for the OCR pipeline.

Tracks:
  - Field-level extraction accuracy (per field type)
  - OCR confidence calibration (predicted vs actual)
  - Unreadable rate (quality gate rejections)
  - Processing latency (p50, p95, p99)
  - Correction rate (% invoices requiring user edits)
  - FUTURE: expert agreement, booking correctness, VAT correctness

Designed for integration with Prometheus (via prometheus_client) or
any metrics backend via the abstract MetricsBackend interface.
"""
from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics backend abstraction
# ---------------------------------------------------------------------------

class MetricsBackend(ABC):
    """Abstract interface for metrics storage/export."""

    @abstractmethod
    def increment_counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        ...

    @abstractmethod
    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        ...

    @abstractmethod
    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        ...


class InMemoryMetricsBackend(MetricsBackend):
    """
    In-memory metrics backend for development and testing.
    Production should use PrometheusMetricsBackend or similar.
    """

    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.histograms: dict[str, list[float]] = defaultdict(list)
        self.gauges: dict[str, float] = {}

    def increment_counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._make_key(name, labels)
        self.counters[key] += value

    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._make_key(name, labels)
        self.histograms[key].append(value)

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._make_key(name, labels)
        self.gauges[key] = value

    @staticmethod
    def _make_key(name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"


# ---------------------------------------------------------------------------
# Core metrics collector
# ---------------------------------------------------------------------------

class MetricName(str, Enum):
    """Canonical metric names."""
    # Counters
    INVOICES_PROCESSED = "scanbonai_invoices_processed_total"
    INVOICES_SUCCEEDED = "scanbonai_invoices_succeeded_total"
    INVOICES_FAILED = "scanbonai_invoices_failed_total"
    QUALITY_GATE_PASS = "scanbonai_quality_gate_pass_total"
    QUALITY_GATE_WARN = "scanbonai_quality_gate_warn_total"
    QUALITY_GATE_FAIL = "scanbonai_quality_gate_fail_total"
    FIELD_EXTRACTIONS = "scanbonai_field_extractions_total"
    FIELD_CORRECTIONS = "scanbonai_field_corrections_total"
    INVOICES_WITH_CORRECTIONS = "scanbonai_invoices_with_corrections_total"

    # Histograms
    PROCESSING_LATENCY_MS = "scanbonai_processing_latency_ms"
    OCR_LATENCY_MS = "scanbonai_ocr_latency_ms"
    EXTRACTION_LATENCY_MS = "scanbonai_extraction_latency_ms"
    FIELD_CONFIDENCE = "scanbonai_field_confidence"

    # Gauges
    FIELD_ACCURACY = "scanbonai_field_accuracy"
    CONFIDENCE_CALIBRATION_ERROR = "scanbonai_confidence_calibration_error"
    UNREADABLE_RATE = "scanbonai_unreadable_rate"
    CORRECTION_RATE = "scanbonai_correction_rate"


@dataclass
class FieldAccuracyTracker:
    """
    Tracks per-field accuracy using an online algorithm.

    Maintains running counts of correct and total extractions
    per field, enabling real-time accuracy computation without
    storing individual results.
    """
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        """Current accuracy as a fraction (0.0 - 1.0)."""
        return self.correct / self.total if self.total > 0 else 0.0

    def record(self, is_correct: bool) -> None:
        """Record a single extraction result."""
        self.total += 1
        if is_correct:
            self.correct += 1


@dataclass
class ConfidenceCalibrationBucket:
    """
    A single bucket for confidence calibration tracking.

    Groups predictions by confidence range (e.g., 0.8-0.9) and
    tracks what fraction were actually correct.
    """
    bucket_low: float
    bucket_high: float
    predicted_correct: int = 0   # number of predictions in this confidence range
    actually_correct: int = 0    # number that were actually correct

    @property
    def expected_accuracy(self) -> float:
        """Midpoint of the confidence range (expected accuracy)."""
        return (self.bucket_low + self.bucket_high) / 2.0

    @property
    def actual_accuracy(self) -> float:
        """Observed accuracy within this bucket."""
        return self.actually_correct / self.predicted_correct if self.predicted_correct > 0 else 0.0

    @property
    def calibration_error(self) -> float:
        """Absolute difference between expected and actual accuracy."""
        return abs(self.expected_accuracy - self.actual_accuracy)


class OCRMetricsCollector:
    """
    Central metrics collector for the ScanbonAI OCR pipeline.

    Tracks all key metrics and provides methods for recording
    pipeline events and computing aggregate statistics.

    Usage:
        collector = OCRMetricsCollector()
        collector.record_pipeline_result(result)
        collector.record_correction(field_name="total_amount", ...)
        report = collector.get_summary_report()
    """

    # Number of buckets for confidence calibration (10 buckets of width 0.1)
    _CALIBRATION_BUCKETS = 10

    def __init__(self, backend: MetricsBackend | None = None) -> None:
        self._backend = backend or InMemoryMetricsBackend()

        # Per-field accuracy trackers
        self._field_accuracy: dict[str, FieldAccuracyTracker] = defaultdict(FieldAccuracyTracker)

        # Confidence calibration buckets (per field)
        self._calibration: dict[str, list[ConfidenceCalibrationBucket]] = {}

        # Latency samples (for percentile computation)
        self._latency_samples: list[float] = []

        # Aggregate counters
        self._total_processed: int = 0
        self._total_succeeded: int = 0
        self._total_quality_fail: int = 0
        self._total_with_corrections: int = 0

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_pipeline_result(
        self,
        success: bool,
        quality_verdict: str,
        processing_time_ms: float,
        ocr_time_ms: float = 0.0,
        extraction_time_ms: float = 0.0,
        provider: str = "unknown",
    ) -> None:
        """
        Record the outcome of a single pipeline run.

        Args:
            success: Whether the pipeline completed successfully.
            quality_verdict: "pass", "warn", or "fail".
            processing_time_ms: Total end-to-end latency.
            ocr_time_ms: Time spent in OCR provider.
            extraction_time_ms: Time spent in structured extraction.
            provider: OCR provider name.
        """
        self._total_processed += 1
        labels = {"provider": provider}

        self._backend.increment_counter(MetricName.INVOICES_PROCESSED, labels=labels)

        if success:
            self._total_succeeded += 1
            self._backend.increment_counter(MetricName.INVOICES_SUCCEEDED, labels=labels)
        else:
            self._backend.increment_counter(MetricName.INVOICES_FAILED, labels=labels)

        # Quality gate
        verdict_metric = {
            "pass": MetricName.QUALITY_GATE_PASS,
            "warn": MetricName.QUALITY_GATE_WARN,
            "fail": MetricName.QUALITY_GATE_FAIL,
        }.get(quality_verdict)
        if verdict_metric:
            self._backend.increment_counter(verdict_metric)

        if quality_verdict == "fail":
            self._total_quality_fail += 1

        # Latency
        self._latency_samples.append(processing_time_ms)
        self._backend.observe_histogram(MetricName.PROCESSING_LATENCY_MS, processing_time_ms, labels)
        if ocr_time_ms > 0:
            self._backend.observe_histogram(MetricName.OCR_LATENCY_MS, ocr_time_ms, labels)
        if extraction_time_ms > 0:
            self._backend.observe_histogram(MetricName.EXTRACTION_LATENCY_MS, extraction_time_ms)

        # Update gauges
        self._update_rate_gauges()

    def record_field_extraction(
        self,
        field_name: str,
        confidence: float,
        is_correct: bool,
    ) -> None:
        """
        Record the outcome of a single field extraction.

        Used to track per-field accuracy and confidence calibration.

        Args:
            field_name: Name of the extracted field (e.g. "total_amount").
            confidence: Model's predicted confidence (0.0 - 1.0).
            is_correct: Whether the extraction was correct (no user edit needed).
        """
        # Field accuracy
        self._field_accuracy[field_name].record(is_correct)
        self._backend.increment_counter(
            MetricName.FIELD_EXTRACTIONS,
            labels={"field": field_name},
        )

        # Confidence calibration
        self._record_calibration(field_name, confidence, is_correct)

        # Confidence histogram
        self._backend.observe_histogram(
            MetricName.FIELD_CONFIDENCE,
            confidence,
            labels={"field": field_name},
        )

        # Update accuracy gauge
        tracker = self._field_accuracy[field_name]
        self._backend.set_gauge(
            MetricName.FIELD_ACCURACY,
            tracker.accuracy,
            labels={"field": field_name},
        )

    def record_correction(
        self,
        field_name: str,
        original_value: str,
        corrected_value: str,
        source: str = "user_edit",
    ) -> None:
        """
        Record a user/admin/expert correction to an extracted field.

        Args:
            field_name: The corrected field name.
            original_value: The value before correction.
            corrected_value: The value after correction.
            source: Correction source ("user_edit", "admin_override", "expert_review").
        """
        self._backend.increment_counter(
            MetricName.FIELD_CORRECTIONS,
            labels={"field": field_name, "source": source},
        )
        logger.debug(
            "Correction recorded: field=%s source=%s '%s' -> '%s'",
            field_name,
            source,
            original_value,
            corrected_value,
        )

    def record_invoice_correction(self) -> None:
        """Record that an invoice required at least one field correction."""
        self._total_with_corrections += 1
        self._backend.increment_counter(MetricName.INVOICES_WITH_CORRECTIONS)
        self._update_rate_gauges()

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_field_accuracy(self, field_name: str) -> float:
        """Get current accuracy for a specific field."""
        return self._field_accuracy[field_name].accuracy

    def get_all_field_accuracies(self) -> dict[str, float]:
        """Get accuracy for all tracked fields."""
        return {
            name: tracker.accuracy
            for name, tracker in self._field_accuracy.items()
        }

    def get_latency_percentiles(self) -> dict[str, float]:
        """
        Compute p50, p95, p99 latency from recorded samples.

        Returns:
            Dict with keys "p50", "p95", "p99" (values in milliseconds).
        """
        if not self._latency_samples:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        sorted_samples = sorted(self._latency_samples)
        n = len(sorted_samples)

        def _percentile(p: float) -> float:
            idx = int(math.ceil(p / 100.0 * n)) - 1
            return sorted_samples[max(0, min(idx, n - 1))]

        return {
            "p50": round(_percentile(50), 2),
            "p95": round(_percentile(95), 2),
            "p99": round(_percentile(99), 2),
        }

    def get_unreadable_rate(self) -> float:
        """Fraction of invoices rejected by the quality gate."""
        if self._total_processed == 0:
            return 0.0
        return self._total_quality_fail / self._total_processed

    def get_correction_rate(self) -> float:
        """Fraction of successfully processed invoices that required edits."""
        if self._total_succeeded == 0:
            return 0.0
        return self._total_with_corrections / self._total_succeeded

    def get_confidence_calibration_error(self, field_name: str) -> float:
        """
        Compute Expected Calibration Error (ECE) for a specific field.

        ECE is the weighted average of per-bucket calibration errors,
        weighted by the number of samples in each bucket.

        Lower is better. 0.0 means perfectly calibrated.
        """
        buckets = self._calibration.get(field_name, [])
        if not buckets:
            return 0.0

        total_samples = sum(b.predicted_correct for b in buckets)
        if total_samples == 0:
            return 0.0

        ece = sum(
            (b.predicted_correct / total_samples) * b.calibration_error
            for b in buckets
            if b.predicted_correct > 0
        )
        return round(ece, 6)

    def get_summary_report(self) -> dict:
        """
        Generate a comprehensive metrics summary.

        Returns:
            Dict containing all tracked metrics in a structured format.
        """
        latency = self.get_latency_percentiles()
        field_accuracies = self.get_all_field_accuracies()

        calibration_errors = {
            field_name: self.get_confidence_calibration_error(field_name)
            for field_name in self._calibration
        }

        return {
            "totals": {
                "processed": self._total_processed,
                "succeeded": self._total_succeeded,
                "quality_rejected": self._total_quality_fail,
                "with_corrections": self._total_with_corrections,
            },
            "rates": {
                "success_rate": (
                    self._total_succeeded / self._total_processed
                    if self._total_processed > 0
                    else 0.0
                ),
                "unreadable_rate": self.get_unreadable_rate(),
                "correction_rate": self.get_correction_rate(),
            },
            "latency_ms": latency,
            "field_accuracy": field_accuracies,
            "confidence_calibration_error": calibration_errors,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_calibration(
        self,
        field_name: str,
        confidence: float,
        is_correct: bool,
    ) -> None:
        """Record a single observation for confidence calibration."""
        if field_name not in self._calibration:
            self._calibration[field_name] = [
                ConfidenceCalibrationBucket(
                    bucket_low=i / self._CALIBRATION_BUCKETS,
                    bucket_high=(i + 1) / self._CALIBRATION_BUCKETS,
                )
                for i in range(self._CALIBRATION_BUCKETS)
            ]

        bucket_idx = min(
            int(confidence * self._CALIBRATION_BUCKETS),
            self._CALIBRATION_BUCKETS - 1,
        )
        bucket = self._calibration[field_name][bucket_idx]
        bucket.predicted_correct += 1
        if is_correct:
            bucket.actually_correct += 1

    def _update_rate_gauges(self) -> None:
        """Update the unreadable rate and correction rate gauges."""
        self._backend.set_gauge(
            MetricName.UNREADABLE_RATE,
            self.get_unreadable_rate(),
        )
        self._backend.set_gauge(
            MetricName.CORRECTION_RATE,
            self.get_correction_rate(),
        )
```

---

## Appendix: File Structure

```
scanbonai/
├── ocr/
│   ├── __init__.py
│   ├── models.py               # Data models (OCRResult, InvoiceData, etc.)
│   ├── quality.py              # Quality gate checks (blur, resolution, skew, exposure)
│   ├── preprocessing.py        # Image pre-processing (deskew, CLAHE, denoising)
│   ├── extraction.py           # LLM-based structured extraction (OCR text -> JSON)
│   ├── pipeline.py             # Top-level pipeline orchestrator
│   ├── metrics.py              # Metrics collection and monitoring
│   ├── feedback.py             # Privacy-first feedback/learning pipeline
│   ├── expert_labels.py        # Expert label system (FUTURE)
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py             # OCRAdapter ABC + OCRProviderError
│   │   ├── orchestrator.py     # Fallback chain orchestrator
│   │   ├── deepseek_ocr2.py    # DeepSeek OCR 2 implementation
│   │   ├── google_vision.py    # Google Vision stub (fallback)
│   │   └── tesseract.py        # Tesseract stub (offline fallback)
│   └── datasets/               # Evaluation dataset loaders (FUTURE)
│       ├── __init__.py
│       ├── sroie.py
│       └── cord.py
└── ...
```

---

## Appendix: Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **DeepSeek OCR 2 as primary** | State-of-the-art on OmniDocBench v1.5 (91.09), OpenAI-compatible API, extremely cost-effective (~$0.028/M input tokens cached), strong layout understanding via Visual Causal Flow architecture |
| **OpenAI-compatible API interface** | Enables drop-in provider switching. Works with DeepSeek API, self-hosted vLLM, or Clarifai endpoints with zero code changes |
| **Quality gate before OCR** | Avoids wasting API calls and latency on unprocessable images; provides actionable feedback to WhatsApp users ("photo is too blurry, please retake") |
| **LLM-based extraction over pure regex** | Invoices have highly variable layouts; LLMs handle this naturally. Regex validates critical fields (IBAN, VAT ID) as a cross-check |
| **Separate OCR + extraction steps** | Allows independent evaluation: OCR accuracy (text fidelity) vs extraction accuracy (field parsing). Also enables swapping either component independently |
| **Confidence scoring via logprobs + heuristics** | LLM logprobs give raw extraction confidence; format validation (IBAN checksum, VAT ID pattern) and cross-field checks (subtotal + VAT = total) refine it |
| **PII never in training sets** | Legal requirement (GDPR/AVG). Anonymisation happens at write time, not query time, so PII cannot leak even if the feedback store is compromised |
| **Prompt tuning before fine-tuning** | Lower risk, faster iteration, no GPU infrastructure needed. Fine-tuning (LoRA) deferred until 5000+ anonymised correction pairs accumulated |
| **DVC for dataset versioning** | Reproducible experiments with git-like semantics. Correction datasets can be tagged, branched, and rolled back alongside model versions |

---

Sources used during research:
- [DeepSeek OCR 2 on Hugging Face](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2)
- [DeepSeek OCR 2 architecture overview (MarkTechPost)](https://www.marktechpost.com/2026/01/30/deepseek-ai-releases-deepseek-ocr-2-with-causal-visual-flow-encoder-for-layout-aware-document-understanding/)
- [DeepSeek OCR 2 hands-on guide (Analytics Vidhya)](https://www.analyticsvidhya.com/blog/2026/01/deepseek-ocr-2/)
- [DeepSeek OCR API usage (Apidog)](https://apidog.com/blog/use-deepseek-ocr-2-api/)
- [SROIE competition (ICDAR 2019)](https://rrc.cvc.uab.es/?ch=13)
- [SROIE dataset on Kaggle](https://www.kaggle.com/datasets/urbikn/sroie-datasetv2)
- [CORD dataset paper (OpenReview)](https://openreview.net/pdf?id=SJl3z659UH)
- [RVL-CDIP dataset (Hugging Face)](https://huggingface.co/datasets/aharley/rvl_cdip)
- [Invoices & Receipts OCR v1 (Hugging Face)](https://huggingface.co/datasets/mychen76/invoices-and-receipts_ocr_v1)
