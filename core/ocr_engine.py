"""
Local OCR Engine for Topology & Table Extraction.

Optimized RapidOCR ONNX pipeline with single-pass in-memory decoding,
smart resolution capping, and zero redundant PNG compression cycles.
"""

import os
import io
import logging
import sys
from typing import List, Dict, Any, Tuple
import numpy as np

# Constrain thread contention before onnxruntime sessions spin up
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("ONNXRUNTIME_INTRA_OP_NUM_THREADS", "2")
os.environ.setdefault("ONNXRUNTIME_INTER_OP_NUM_THREADS", "1")

from PIL import Image, ImageEnhance
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
        try:
            _ocr_engine = RapidOCR(use_angle_cls=False, intra_op_num_threads=2)
        except TypeError:
            _ocr_engine = RapidOCR(use_angle_cls=False)
    return _ocr_engine


def preprocess_image_to_bgr(input_data: Any) -> np.ndarray:
    """Single-pass in-memory loader and normalizer. Returns a BGR numpy array.
    Avoids redundant cv2.imencode('.png') and PIL re-opening cycles.
    """
    raw_bytes = None
    img_bgr = None

    if isinstance(input_data, (bytes, bytearray)):
        raw_bytes = bytes(input_data)
    elif hasattr(input_data, "getvalue"):
        raw_bytes = input_data.getvalue()
    elif hasattr(input_data, "read"):
        input_data.seek(0)
        raw_bytes = input_data.read()
    elif isinstance(input_data, np.ndarray):
        img_bgr = input_data
    elif isinstance(input_data, Image.Image):
        rgb_np = np.array(input_data.convert("RGB"))
        return cv2.cvtColor(rgb_np, cv2.COLOR_RGB2BGR) if HAS_CV2 else rgb_np[:, :, ::-1]
    else:
        # File path
        if HAS_CV2:
            img_bgr = cv2.imread(str(input_data), cv2.IMREAD_COLOR)
        else:
            return np.array(Image.open(str(input_data)).convert("RGB"))[:, :, ::-1]

    if raw_bytes is not None:
        if len(raw_bytes) == 0:
            return None
        if HAS_CV2:
            nparr = np.frombuffer(raw_bytes, np.uint8)
            img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        else:
            pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            return np.array(pil_img)[:, :, ::-1]

    if img_bgr is None:
        return None

    # Smart single-pass resolution normalization (optimal for RapidOCR DBNet)
    h, w = img_bgr.shape[:2]
    if w < 1400 and HAS_CV2:
        scale = min(2.0, 1800.0 / w)
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
    elif w > 2200 and HAS_CV2:
        scale = 2000.0 / w
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    return img_bgr


def preprocess_topology_image(image_bytes: bytes) -> bytes:
    """Backward compatibility wrapper. Preserves existing signature if called externally."""
    return image_bytes


class LocalOCREngine:
    def __init__(self, min_confidence_threshold: float = 0.4):
        self.min_confidence_threshold = min_confidence_threshold

    def _load_image(self, input_data: Any) -> np.ndarray:
        return preprocess_image_to_bgr(input_data)

    def _enhance_contrast(self, img_bgr: np.ndarray) -> np.ndarray:
        if not HAS_CV2 or img_bgr is None:
            return img_bgr
        lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        return cv2.merge((l, a, b))

    @staticmethod
    def _auto_invert_dark_mode(img_bgr: np.ndarray) -> np.ndarray:
        if img_bgr is None or not HAS_CV2:
            return img_bgr
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        if np.mean(gray) < 100:
            return cv2.bitwise_not(img_bgr)
        return img_bgr

    @staticmethod
    def _spatial_row_grouping(raw_items: List[Any], y_threshold: float = 12.0) -> List[str]:
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

        return [" | ".join(cell["text"] for cell in row) for row in rows]

    def _parse_result_with_boxes(self, result) -> Tuple[List[str], List[float], List[Dict]]:
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
                    xmin = float(np.min(box_np[:, 0]))
                    ymin = float(np.min(box_np[:, 1]))
                    xmax = float(np.max(box_np[:, 0]))
                    ymax = float(np.max(box_np[:, 1]))
                    raw_tokens.append({
                        "text": str(text).strip(),
                        "box": [xmin, ymin, xmax - xmin, ymax - ymin],
                        "conf": float(conf),
                    })
        grouped_lines = self._spatial_row_grouping(result)
        return grouped_lines, confs, raw_tokens

    def extract_text_with_metadata(self, image_input: Any) -> Dict[str, Any]:
        engine = get_ocr_engine()
        img_bgr = self._load_image(image_input)
        if img_bgr is None:
            return {
                "text": "",
                "average_confidence": 0.0,
                "word_count": 0,
                "lines": [],
                "raw_tokens": [],
                "image_height": 0,
            }

        processed_bgr = self._auto_invert_dark_mode(img_bgr)
        result, _ = engine(processed_bgr)
        detected_lines, all_confidences, all_raw_tokens = self._parse_result_with_boxes(result)

        if not detected_lines:
            enhanced_bgr = self._enhance_contrast(processed_bgr)
            result, _ = engine(enhanced_bgr)
            detected_lines, all_confidences, all_raw_tokens = self._parse_result_with_boxes(result)

        avg_conf = (sum(all_confidences) / len(all_confidences)) if all_confidences else 0.0
        full_text = "\n".join(detected_lines).strip()
        image_height = int(processed_bgr.shape[0])
        image_width = int(processed_bgr.shape[1])

        return {
            "text": full_text,
            "average_confidence": round(avg_conf, 2),
            "word_count": len(full_text.split()),
            "lines": detected_lines,
            "raw_tokens": all_raw_tokens,
            "image_height": image_height,
            "image_width": image_width,
        }

    def assess_quality(self, ocr_result: Dict[str, Any], force_pass: bool = False) -> Tuple[bool, str]:
        if force_pass:
            return True, "Quality checks bypassed via Force Pass."
        avg_conf = ocr_result.get("average_confidence", 0.0)
        word_count = ocr_result.get("word_count", 0)
        text = ocr_result.get("text", "")

        if word_count < 3 or len(text) < 10:
            return False, "Low word count detected. Image may be unreadable or empty."

        conf_pct = avg_conf * 100.0 if avg_conf <= 1.0 else avg_conf
        threshold_pct = (
            self.min_confidence_threshold * 100.0
            if self.min_confidence_threshold <= 1.0
            else self.min_confidence_threshold
        )
        if conf_pct < threshold_pct:
            return False, f"Low OCR confidence ({round(conf_pct, 1)}% < {round(threshold_pct, 1)}%)."

        return True, f"OCR extraction passed with confidence {round(conf_pct, 1)}%."


def run_local_ocr_pipeline(image_files: List[Any], force_pass: bool = False) -> Dict[str, Any]:
    engine = LocalOCREngine()
    aggregated_lines: List[str] = []
    all_raw_tokens: List[Dict] = []
    total_conf: float = 0.0
    passed_count: int = 0
    errors: List[str] = []
    image_heights: List[int] = []
    image_widths: List[int] = []

    for idx, img_file in enumerate(image_files):
        try:
            res = engine.extract_text_with_metadata(img_file)
            ok, msg = engine.assess_quality(res, force_pass=force_pass)
            if not ok:
                errors.append(f"Image #{idx + 1}: {msg}")
            else:
                passed_count += 1
                if res.get("text") and len(res["text"].strip()) > 0:
                    aggregated_lines.append(res["text"])
                if res.get("raw_tokens"):
                    for t in res["raw_tokens"]:
                        t["_src"] = idx
                    all_raw_tokens.extend(res["raw_tokens"])
                total_conf += res["average_confidence"]
                image_heights.append(res.get("image_height", 0))
                image_widths.append(res.get("image_width", 0))
        except Exception as exc:
            errors.append(f"Image #{idx + 1} processing error: {str(exc)}")
            image_heights.append(0)

    combined_text = "\n\n".join(aggregated_lines).strip()
    avg_conf = round(total_conf / passed_count, 2) if passed_count > 0 else 0.0

    return {
        "success": bool(combined_text and (len(errors) == 0 or force_pass)),
        "combined_text": combined_text,
        "average_confidence": avg_conf,
        "processed_count": len(image_files),
        "passed_count": passed_count,
        "errors": errors,
        "raw_tokens": all_raw_tokens,
        "image_heights": image_heights,
        "image_widths": image_widths,
    }
