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
    def __init__(self, min_confidence_threshold: float = 0.4):
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
    def _auto_invert_dark_mode(img_bgr: np.ndarray) -> np.ndarray:
        """Auto-detect dark mode UI by average luminance and invert for OCR enhancement."""
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if HAS_CV2 else np.mean(img_bgr, axis=-1)
        if np.mean(gray) < 100:
            return cv2.bitwise_not(img_bgr) if HAS_CV2 else (255 - img_bgr).astype(np.uint8)
        return img_bgr

    @staticmethod
    def _spatial_row_grouping(raw_items: List[Any], y_threshold: float = 12.0) -> List[str]:
        """
        Reconstruct 2D spatial layout from Bounding Boxes.
        Groups text blocks sharing similar Y coordinates into markdown virtual table rows,
        sorted strictly left-to-right (by X coordinate).
        """
        boxes_with_text = []
        for item in raw_items:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            box, text = item[0], item[1]
            if not text or not str(text).strip():
                continue
            box_np = np.array(box)
            center_x = float(np.mean(box_np[:, 0]))
            center_y = float(np.mean(box_np[:, 1]))
            boxes_with_text.append({
                "text": str(text).strip(),
                "x": center_x,
                "y": center_y,
            })

        if not boxes_with_text:
            return []

        boxes_with_text.sort(key=lambda b: b["y"])

        rows: List[List[Dict[str, Any]]] = []
        current_row: List[Dict[str, Any]] = [boxes_with_text[0]]
        current_y = boxes_with_text[0]["y"]

        for item in boxes_with_text[1:]:
            if abs(item["y"] - current_y) <= y_threshold:
                current_row.append(item)
            else:
                current_row.sort(key=lambda b: b["x"])
                rows.append(current_row)
                current_row = [item]
                current_y = item["y"]

        if current_row:
            current_row.sort(key=lambda b: b["x"])
            rows.append(current_row)

        reconstructed_lines = [
            " | ".join(cell["text"] for cell in row)
            for row in rows
        ]
        return reconstructed_lines

    def _parse_result_with_boxes(self, result) -> Tuple[List[str], List[float], List[Dict]]:
        """Parse RapidOCR result into spatially grouped lines, confidences, and raw token boxes."""
        confs: List[float] = []
        raw_tokens: List[Dict] = []
        if result is None:
            return [], confs, raw_tokens
        for item in result:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            conf = item[2] if len(item) >= 3 else 0.0
            if isinstance(conf, (int, float)) and conf > 0:
                confs.append(float(conf))
            box = item[0] if len(item) >= 1 else None
            text = item[1] if len(item) >= 2 else ""
            if box is not None and text:
                box_np = np.array(box)
                if box_np.ndim == 2 and box_np.shape == (4, 2):
                    x0, y0 = box_np[0]
                    x1, y1 = box_np[1]
                    x2, y2 = box_np[2]
                    x3, y3 = box_np[3]
                    xmin = float(min(x0, x1, x2, x3))
                    ymin = float(min(y0, y1, y2, y3))
                    xmax = float(max(x0, x1, x2, x3))
                    ymax = float(max(y0, y1, y2, y3))
                    raw_tokens.append({
                        "text": str(text).strip(),
                        "box": [xmin, ymin, xmax - xmin, ymax - ymin],
                    })
        grouped_lines = self._spatial_row_grouping(result)
        return grouped_lines, confs, raw_tokens

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
        all_raw_tokens: List[Dict] = []

        # Auto-invert dark mode if running against a dark dashboard
        processed_bgr = self._auto_invert_dark_mode(img_bgr)

        # Pass 1: standard inference with spatial grouping
        result, _ = engine(processed_bgr)
        detected_lines, all_confidences, all_raw_tokens = self._parse_result_with_boxes(result)
        import sys as _sys
        _sys.stdout.write(f"[OCR ENGINE] Lines detected (Spatial): {len(detected_lines)}, preview: {detected_lines[:2] if detected_lines else []}\n")
        _sys.stdout.flush()
        _logger.debug("[OCR DEBUG] Pass 1: %d lines", len(detected_lines))
        _flush()

        # Pass 2: contrast-enhanced fallback with spatial grouping
        if not detected_lines:
            enhanced_bgr = self._enhance_contrast(processed_bgr)
            result, _ = engine(enhanced_bgr)
            detected_lines, all_confidences, all_raw_tokens = self._parse_result_with_boxes(result)
            import sys as _sys
            _sys.stdout.write(f"[OCR ENGINE] Lines detected (Pass 2 Spatial): {len(detected_lines)}, preview: {detected_lines[:2] if detected_lines else []}\n")
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
            "raw_tokens": all_raw_tokens,
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

        # Normalize confidence to percentage scale for comparison
        conf_pct = avg_conf * 100.0 if avg_conf <= 1.0 else avg_conf
        threshold_pct = (
            self.min_confidence_threshold * 100.0
            if self.min_confidence_threshold <= 1.0
            else self.min_confidence_threshold
        )

        if conf_pct < threshold_pct:
            return (
                False,
                f"Low OCR confidence ({round(conf_pct, 1)}% < {round(threshold_pct, 1)}%). "
                "Image may be blurry or low contrast.",
            )

        return True, f"OCR extraction passed with confidence {round(conf_pct, 1)}%."


def preprocess_topology_image(image_bytes: bytes) -> bytes:
    """Enhance screenshot quality locally via OpenCV before text recognition.

    Applies grayscale conversion, CLAHE contrast enhancement, and 1.5x
    bicubic upscaling to clearly separate joined characters. Processing is
    strictly in-memory so it does not affect the Sanitizer Vault.
    """
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return image_bytes

        # 1. Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 2. Contrast Limited Adaptive Histogram Equalization (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # 3. 1.5x bicubic upscaling to clearly separate joined characters
        h, w = enhanced.shape[:2]
        resized = cv2.resize(enhanced, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_CUBIC)

        success, encoded = cv2.imencode('.png', resized)
        return encoded.tobytes() if success else image_bytes
    except Exception as exc:
        _logger.warning("[OCR PREPROCESS] Preprocessing failed (%s), returning raw bytes.", exc)
        return image_bytes


def run_local_ocr_pipeline(
    image_files: List[Any], force_pass: bool = False
) -> Dict[str, Any]:
    """
    Batch helper to process uploaded screenshots into a combined plain text buffer.
    Each image is preprocessed (grayscale + CLAHE + upscale) before OCR to improve
    character separation and overall readability.
    """
    engine = LocalOCREngine()
    aggregated_lines: List[str] = []
    all_raw_tokens: List[Dict] = []
    total_conf: float = 0.0
    passed_count: int = 0
    errors: List[str] = []

    for idx, img_file in enumerate(image_files):
        try:
            # Preprocess image bytes in memory before sending to OCR engine
            if isinstance(img_file, (bytes, bytearray)):
                img_file = preprocess_topology_image(bytes(img_file))
            elif hasattr(img_file, "read"):
                original_bytes = img_file.read()
                img_file.seek(0)
                img_file = preprocess_topology_image(original_bytes)
            elif hasattr(img_file, "getvalue"):
                original_bytes = img_file.getvalue()
                img_file.seek(0)
                img_file = preprocess_topology_image(original_bytes)
            res = engine.extract_text_with_metadata(img_file)
            ok, msg = engine.assess_quality(res, force_pass=force_pass)
            if not ok:
                errors.append(f"Image #{idx + 1}: {msg}")
            else:
                passed_count += 1
                # Defensive: always include text when non-empty, regardless of force_pass
                if res.get("text") and len(res["text"].strip()) > 0:
                    aggregated_lines.append(res["text"])
                if res.get("raw_tokens"):
                    for t in res["raw_tokens"]:
                        t["_src"] = idx
                    all_raw_tokens.extend(res["raw_tokens"])
                total_conf += res["average_confidence"]
        except Exception as exc:
            errors.append(f"Image #{idx + 1} processing error: {str(exc)}")

    combined_text = "\n\n".join(aggregated_lines).strip()
    avg_conf = (
        round(total_conf / passed_count, 2) if passed_count > 0 else 0.0
    )

    return {
        "success": bool(combined_text and (len(errors) == 0 or force_pass)),
        "combined_text": combined_text,
        "average_confidence": avg_conf,
        "processed_count": len(image_files),
        "passed_count": passed_count,
        "errors": errors,
        "raw_tokens": all_raw_tokens,
    }
