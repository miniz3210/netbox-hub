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
import numpy as np
from typing import List, Dict, Any, Tuple
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

_logger = logging.getLogger(__name__) or logging

_ocr_engine = None


def get_ocr_engine() -> RapidOCR:
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


class LocalOCREngine:
    def __init__(self, min_confidence_threshold: float = 40.0):
        self.min_confidence_threshold = min_confidence_threshold

    @staticmethod
    def _load_image(image_input: Any) -> np.ndarray:
        """Load image from bytes, file-like, PIL Image, or path and return BGR numpy array."""
        if isinstance(image_input, (bytes, bytearray)):
            pil_img = Image.open(io.BytesIO(image_input)).convert("RGB")
        elif hasattr(image_input, "read"):
            image_input.seek(0)
            pil_img = Image.open(image_input).convert("RGB")
        elif isinstance(image_input, Image.Image):
            pil_img = image_input.convert("RGB")
        else:
            pil_img = Image.open(str(image_input)).convert("RGB")

        rgb_np = np.array(pil_img)
        # Ensure contiguous memory layout for ONNX runtime compatibility
        rgb_np = np.ascontiguousarray(rgb_np)

        if rgb_np.ndim == 2:
            # Grayscale: convert to 3-channel BGR
            if HAS_CV2:
                return cv2.cvtColor(rgb_np, cv2.COLOR_GRAY2BGR)
            return np.stack([rgb_np] * 3, axis=-1)[..., ::-1]
        elif rgb_np.shape[-1] == 4:
            # RGBA -> drop alpha channel -> RGB -> BGR
            rgb_only = rgb_np[:, :, :3]
            if HAS_CV2:
                return cv2.cvtColor(rgb_only, cv2.COLOR_RGB2BGR)
            return rgb_only[:, :, ::-1]
        elif rgb_np.shape[-1] == 3:
            if HAS_CV2:
                return cv2.cvtColor(rgb_np, cv2.COLOR_RGB2BGR)
            return rgb_np[:, :, ::-1]
        # Fallback: return as-is (contiguous uint8)
        return rgb_np

    def _enhance_contrast(self, img_bgr: np.ndarray) -> np.ndarray:
        """Apply CLAHE contrast enhancement for low-contrast topology labels."""
        if not HAS_CV2:
            # Software fallback: simple histogram equalization via PIL
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

    def extract_text_with_metadata(
        self, image_input: Any
    ) -> Dict[str, Any]:
        """
        Extract text, line-level segments, and overall confidence metrics
        using RapidOCR (ONNX runtime) with contrast-enhancement fallback.
        """
        engine = get_ocr_engine()
        img_bgr = self._load_image(image_input)

        # First pass: standard inference
        result, elapse_list = engine(img_bgr)

        detected_lines: List[str] = []
        all_confidences: List[float] = []

        if result is not None:
            for item in result:
                # item is [box_coords, text, confidence_float]
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                text = item[1]
                conf = item[2] if len(item) >= 3 else 0.0
                if text and isinstance(text, str) and text.strip():
                    detected_lines.append(text.strip())
                    if isinstance(conf, (int, float)) and conf > 0:
                        all_confidences.append(float(conf))

        _logger.debug("[OCR DEBUG] First pass: %d lines detected", len(detected_lines))

        # Fallback: enhanced contrast if first pass yielded nothing
        if not detected_lines:
            enhanced_bgr = self._enhance_contrast(img_bgr)
            result, elapse_list = engine(enhanced_bgr)
            if result is not None:
                for item in result:
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        continue
                    text = item[1]
                    conf = item[2] if len(item) >= 3 else 0.0
                    if text and isinstance(text, str) and text.strip():
                        detected_lines.append(text.strip())
                        if isinstance(conf, (int, float)) and conf > 0:
                            all_confidences.append(float(conf))

        _logger.debug("[OCR DEBUG] After fallback: %d lines, total text len=%d", len(detected_lines), len("\n".join(detected_lines)))

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
