"""
Image quality gate service for ScanbonAI.

Before sending an invoice image to the OCR engine we run a series of fast,
local checks to catch obviously poor-quality uploads early.  This saves API
costs and gives users immediate feedback.

Quality gates
-------------
1. **Blur detection** – Laplacian variance; low variance → blurry.
2. **Resolution check** – Minimum pixel dimensions.
3. **Skew detection** – Hough-line based angle estimation.

All checks operate on numpy arrays via OpenCV.  Pillow is used only for the
initial file → numpy conversion so we stay library-agnostic for callers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import cv2
import numpy as np
import structlog
from PIL import Image

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tunable thresholds (can be overridden via app settings in a later iteration)
# ---------------------------------------------------------------------------

# Laplacian variance below this → classified as blurry
BLUR_THRESHOLD: Final[float] = 100.0

# Minimum acceptable short-edge pixel dimension
MIN_SHORT_SIDE_PX: Final[int] = 600

# Maximum acceptable skew angle in degrees (absolute value)
MAX_SKEW_DEGREES: Final[float] = 10.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class QualityResult:
    """
    Aggregated result of all quality checks for a single invoice image.

    Attributes
    ----------
    passed:
        ``True`` only when every individual gate passed.
    blur_score:
        Laplacian variance.  Higher is sharper.  ``None`` on error.
    resolution_ok:
        ``True`` when both dimensions meet the minimum requirement.
    skew_angle:
        Estimated skew in degrees (positive = clockwise).  ``None`` on error.
    failure_reasons:
        Human-readable strings for each gate that failed.
    width:
        Detected image width in pixels.
    height:
        Detected image height in pixels.
    elapsed_ms:
        Total wall-clock time for all checks combined.
    """

    passed: bool
    blur_score: float | None = None
    resolution_ok: bool | None = None
    skew_angle: float | None = None
    failure_reasons: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def check_blur(image: np.ndarray) -> float:
    """
    Compute the Laplacian variance of a grayscale image.

    A high variance means the image contains many edges → sharp.
    A low variance means edges are smoothed → blurry.

    Parameters
    ----------
    image:
        Grayscale numpy array (dtype uint8, shape H×W).

    Returns
    -------
    float
        Laplacian variance.  Compare against ``BLUR_THRESHOLD``.
    """
    lap = cv2.Laplacian(image, cv2.CV_64F)
    return float(lap.var())


def check_resolution(image: np.ndarray) -> bool:
    """
    Return ``True`` when the image short-side meets the minimum requirement.

    Parameters
    ----------
    image:
        Grayscale or BGR numpy array.

    Returns
    -------
    bool
        ``True`` if the shorter dimension is ≥ ``MIN_SHORT_SIDE_PX``.
    """
    h, w = image.shape[:2]
    short_side = min(h, w)
    return short_side >= MIN_SHORT_SIDE_PX


def check_skew(image: np.ndarray) -> float:
    """
    Estimate the skew angle of text lines in a grayscale image.

    Uses binary thresholding + Hough-line transform to detect dominant line
    orientations and returns the median angle offset from horizontal.

    Parameters
    ----------
    image:
        Grayscale numpy array (dtype uint8, shape H×W).

    Returns
    -------
    float
        Estimated skew angle in degrees.  Positive = clockwise tilt.
        Returns 0.0 when no lines are detected.
    """
    # Binarise
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Dilate horizontally to merge text fragments into line segments
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1))
    dilated = cv2.dilate(binary, kernel, iterations=1)

    # Probabilistic Hough line transform
    lines = cv2.HoughLinesP(
        dilated,
        rho=1,
        theta=np.pi / 180,
        threshold=50,
        minLineLength=100,
        maxLineGap=20,
    )

    if lines is None or len(lines) == 0:
        logger.debug("quality_check.skew.no_lines_detected")
        return 0.0

    angles: list[float] = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 != x1:
            angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            # Keep only near-horizontal lines (within ±45°)
            if abs(angle) <= 45.0:
                angles.append(angle)

    if not angles:
        return 0.0

    return float(np.median(angles))


# ---------------------------------------------------------------------------
# Composite quality assessment
# ---------------------------------------------------------------------------


def assess_quality(image_path: str | Path) -> QualityResult:
    """
    Run all quality gates against an image on disk.

    Parameters
    ----------
    image_path:
        Absolute path to the image file.

    Returns
    -------
    QualityResult
        Aggregated result including individual scores and a ``passed`` flag.

    Raises
    ------
    FileNotFoundError
        If ``image_path`` does not exist.
    ValueError
        If the file cannot be decoded as an image.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    log = logger.bind(image_path=str(path))
    t0 = time.monotonic()

    # Load via Pillow (handles EXIF rotation), then convert to numpy
    try:
        pil_img = Image.open(path).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Cannot open image {path}: {exc}") from exc

    width, height = pil_img.size
    np_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(np_bgr, cv2.COLOR_BGR2GRAY)

    failure_reasons: list[str] = []

    # --- Gate 1: Blur ---
    blur_score: float | None = None
    try:
        blur_score = check_blur(gray)
        if blur_score < BLUR_THRESHOLD:
            failure_reasons.append(
                f"Image is too blurry (Laplacian variance {blur_score:.1f} < {BLUR_THRESHOLD})."
            )
        log.debug("quality_check.blur", score=blur_score)
    except Exception as exc:
        logger.warning("quality_check.blur.error", error=str(exc))

    # --- Gate 2: Resolution ---
    resolution_ok: bool | None = None
    try:
        resolution_ok = check_resolution(gray)
        if not resolution_ok:
            short_side = min(width, height)
            failure_reasons.append(
                f"Resolution too low (short side {short_side}px < {MIN_SHORT_SIDE_PX}px)."
            )
        log.debug("quality_check.resolution", ok=resolution_ok, w=width, h=height)
    except Exception as exc:
        logger.warning("quality_check.resolution.error", error=str(exc))

    # --- Gate 3: Skew ---
    skew_angle: float | None = None
    try:
        skew_angle = check_skew(gray)
        if abs(skew_angle) > MAX_SKEW_DEGREES:
            failure_reasons.append(
                f"Image is too skewed ({skew_angle:.1f}° > ±{MAX_SKEW_DEGREES}°)."
            )
        log.debug("quality_check.skew", angle=skew_angle)
    except Exception as exc:
        logger.warning("quality_check.skew.error", error=str(exc))

    passed = len(failure_reasons) == 0
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    log.info(
        "quality_check.complete",
        passed=passed,
        blur=blur_score,
        resolution_ok=resolution_ok,
        skew=skew_angle,
        elapsed_ms=elapsed_ms,
        failures=failure_reasons,
    )

    return QualityResult(
        passed=passed,
        blur_score=blur_score,
        resolution_ok=resolution_ok,
        skew_angle=skew_angle,
        failure_reasons=failure_reasons,
        width=width,
        height=height,
        elapsed_ms=elapsed_ms,
    )
