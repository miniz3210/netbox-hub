"""
Local OCR Engine for Topology & Table Extraction.

Replaces legacy Tesseract with RapidOCR (ONNX runtime).
RapidOCR provides state-of-the-art text line detection and recognition for
modern UI dashboards (capturing low-contrast gray text, colons, and PCI
locations cleanly) while remaining 100% local, lightweight, and preserving
the local Sanitizer Vault pipeline.
"""

import io
import logging
import sys
import numpy as np
from typing import List, Dict, Any, Tuple
from PIL import Image, ImageEnhance
from rapidocr_onnxruntime import RapidOCR

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

_logger = logging.getLogger(__name__) or logging

_ocr_engine = None

# Minimum image width; images below this threshold are upscaled for better detection
_MIN_OCR_WIDTH = 2000
_UPSCALE_FACTOR = 2.0


def _flush():
    sys.stdout.flush()


def get_ocr_engine() -> RapidOCR:
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


class LocalOCREngine:
    def __init__(self, min_confidence_threshold: float = 40.0):
        self.min_confidence_threshold = min_confidence_threshold

    @staticmethod
    def _load_image(input_data: Any) -> np.ndarray:
        """Load image from bytes, file-like, PIL Image, or path and return BGR numpy array.

        Images narrower than _MIN_OCR_WIDTH are upscaled by _UPSCALE_FACTOR using
        bicubic interpolation so that small hypervisor UI labels are detectable
        by RapidOCR's DBNet text-line detector.
        """
        import sys as _sys

        img_bytes = None
        if isinstance(input_data, (bytes, bytearray)):
            img_bytes = bytes(input_data)
        elif hasattr(input_data, "getvalue"):
            try:
                img_bytes = input_data.getvalue()
            except Exception:
                img_bytes = None
        elif hasattr(input_data, "read"):
            try:
                input_data.seek(0)
                img_bytes = input_data.read()
            except Exception:
                img_bytes = None
        else:
            # Assume path-like / string
            try:
                pil_img = Image.open(str(input_data)).convert("RGB")
                img_bytes = None  # path handled directly below
            except Exception:
                img_bytes = None

        if img_bytes is not None and len(img_bytes) == 0:
            _sys.stdout.write("[OCR ENGINE ERROR] Received 0-byte image payload!\n")
            _sys.stdout.flush()
            return None

        if img_bytes is not None and len(img_bytes) > 0:
            try:
                pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            except Exception:
                pil_img = Image.open(str(input_data)).convert("RGB")
        elif isinstance(input_data, Image.Image):
            pil_img = input_data.convert("RGB")
        else:
            pil_img = Image.open(str(input_data)).convert("RGB")

        # Upscale small images before colour-space conversion so interpolation
        # operates on the larger pixel grid.
        w, h = pil_img.size
        if w < _MIN_OCR_WIDTH:
            new_w = int(w * _UPSCALE_FACTOR)
            new_h = int(h * _UPSCALE_FACTOR)
            pil_img = pil_img.resize((new_w, new_h), Image.Resampling.BICUBIC)
            _logger.debug("[OCR DEBUG] Upscaled %dx%d -> %dx%d", w, h, new_w, new_h)
            _flush()

        rgb_np = np.array(pil_img)
        rgb_np = np.ascontiguousarray(rgb_np)

        if rgb_np.ndim == 2:
            if HAS_CV2:
                return cv2.cvtColor(rgb_np, cv2.COLOR_GRAY2BGR)
            return np.stack([rgb_np] * 3, axis=-1)
        elif rgb_np.shape[-1] == 4:
            rgb_only = rgb_np[:, :, :3]
            if HAS_CV2:
                return cv2.cvtColor(rgb_only, cv2.COLOR_RGB2BGR)
            return rgb_only[:, :, ::-1]
        elif rgb_np.shape[-1] == 3:
            if HAS_CV2:
                return cv2.cvtColor(rgb_np, cv2.COLOR_RGB2BGR)
            return rgb_np[:, :, ::-1]
        return rgb_np

    def _enhance_contrast(self, img_bgr: np.ndarray) -> np.ndarray:
        """Apply CLAHE contrast enhancement for low-contrast topology labels."""
        if not HAS_CV2:
            pil_img = Image.fromarray(img_bgr[..., ::-1] if img_bgr.shape[-1] == 3 else img_bgr)
            pil_img = ImageEnhance.Contrast(pil_img).enhance(2.0)
            pil_img = ImageEnhance.Sharpness(pil_img).enhance(1.5)
            arr = np.array(pil_img)
            if arr.ndim == 2:
                arr = np.stack([arr] * 3, axis=-1)
            return arr
        lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        return cv2.merge((l, a, b))

    @staticmethod
    def _parse_result(result) -> Tuple[List[str], List[float]]:
        """Parse RapidOCR result into (lines, confidences). Handles None, nested lists."""
        lines: List[str] = []
        confs: List[float] = []
        if result is None:
            return lines, confs
        for item in result:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            text = item[1]
            conf = item[2] if len(item) >= 3 else 0.0
            if text and isinstance(text, str) and text.strip():
                lines.append(text.strip())
                if isinstance(conf, (int, float)) and conf > 0:
                    confs.append(float(conf))
        return lines, confs

    def extract_text_with_metadata(
        self, image_input: Any
    ) -> Dict[str, Any]:
        """
        Extract text using a two-pass strategy:
          Pass 1 — standard inference on upscaled BGR image.
          Pass 2 — CLAHE-boosted contrast on upscaled BGR image (fallback).
        """
        engine = get_ocr_engine()
        img_bgr = self._load_image(image_input)

        detected_lines: List[str] = []
        all_confidences: List[float] = []

        # Pass 1: standard inference
        result, _ = engine(img_bgr)
        detected_lines, all_confidences = self._parse_result(result)
        import sys as _sys
        _sys.stdout.write(f"[OCR ENGINE] Lines detected: {len(detected_lines)}, preview: {detected_lines[:2] if detected_lines else []}\n")
        _sys.stdout.flush()
        _logger.debug("[OCR DEBUG] Pass 1: %d lines", len(detected_lines))
        _flush()

        # Pass 2: contrast-enhanced fallback
        if not detected_lines:
            enhanced_bgr = self._enhance_contrast(img_bgr)
            result, _ = engine(enhanced_bgr)
            detected_lines, all_confidences = self._parse_result(result)
            import sys as _sys
            _sys.stdout.write(f"[OCR ENGINE] Lines detected (Pass 2): {len(detected_lines)}, preview: {detected_lines[:2] if detected_lines else []}\n")
            _sys.stdout.flush()
            _logger.debug("[OCR DEBUG] Pass 2 (enhanced): %d lines", len(detected_lines))
            _flush()

        avg_conf = (
            sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        )
        full_text = "\n".join(detected_lines).strip()

        return {
            "text": full_text,
            "average_confidence": round(avg_conf, 2),
            "word_count": len(full_text.split()),
            "lines": detected_lines,
        }

    def assess_quality(
        self, ocr_result: Dict[str, Any], force_pass: bool = False
    ) -> Tuple[bool, str]:
        """
        Evaluates extraction adequacy against word count and confidence metrics.
        """
        if force_pass:
            return True, "Quality checks bypassed via Force Pass."

        avg_conf = ocr_result.get("average_confidence", 0.0)
        word_count = ocr_result.get("word_count", 0)
        text = ocr_result.get("text", "")

        if word_count < 3 or len(text) < 10:
            return False, "Low word count detected. Image may be unreadable or empty."

        if avg_conf < self.min_confidence_threshold:
            return (
                False,
                f"Low OCR confidence ({avg_conf}% < {self.min_confidence_threshold}%). "
                "Image may be blurry or low contrast.",
            )

        return True, f"OCR extraction passed with confidence {avg_conf}%."


def run_local_ocr_pipeline(
    image_files: List[Any], force_pass: bool = False
) -> Dict[str, Any]:
    """
    Batch helper to process uploaded screenshots into a combined plain text buffer.
    """
    engine = LocalOCREngine()
    aggregated_lines: List[str] = []
    total_conf: float = 0.0
    passed_count: int = 0
    errors: List[str] = []

    for idx, img_file in enumerate(image_files):
        try:
            res = engine.extract_text_with_metadata(img_file)
            ok, msg = engine.assess_quality(res, force_pass=force_pass)
            if not ok:
                errors.append(f"Image #{idx + 1}: {msg}")
            else:
                passed_count += 1
                aggregated_lines.append(res["text"])
                total_conf += res["average_confidence"]
        except Exception as exc:
            errors.append(f"Image #{idx + 1} processing error: {str(exc)}")

    combined_text = "\n\n".join(aggregated_lines).strip()
    avg_conf = round(total_conf / passed_count, 2) if passed_count > 0 else 0.0

    return {
        "success": bool(combined_text and (len(errors) == 0 or force_pass)),
        "combined_text": combined_text,
        "average_confidence": avg_conf,
        "processed_count": len(image_files),
        "passed_count": passed_count,
        "errors": errors,
    }
