"""
Local OCR Engine for Topology & Table Extraction.

Replaces legacy Tesseract with RapidOCR (ONNX runtime).
RapidOCR provides state-of-the-art text line detection and recognition for
modern UI dashboards (capturing low-contrast gray text, colons, and PCI
locations cleanly) while remaining 100% local, lightweight, and preserving
the local Sanitizer Vault pipeline.
"""

import io
import numpy as np
from typing import List, Dict, Any, Tuple
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

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
        # Convert to BGR for OpenCV compatibility with RapidOCR
        rgb_np = np.array(pil_img)
        if rgb_np.shape[-1] == 3:
            return cv2.cvtColor(rgb_np, cv2.COLOR_RGB2BGR)
        elif rgb_np.shape[-1] == 4:
            # Premultiplied alpha images: convert RGBA -> RGB -> BGR
            pil_img = Image.fromarray(rgb_np[:, :, :3])
            return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return cv2.cvtColor(rgb_np, cv2.COLOR_GRAY2BGR)

    def _enhance_contrast(self, img_bgr: np.ndarray) -> np.ndarray:
        """Apply CLAHE contrast enhancement for low-contrast topology labels."""
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

        extracted_words: List[str] = []
        confidences: List[float] = []
        valid_lines: List[str] = []

        if result:
            for line in result:
                if line and len(line) >= 2:
                    text = line[1]
                    conf = float(line[2]) if len(line) >= 3 and line[2] else 0.0
                    if text and str(text).strip():
                        extracted_words.extend(str(text).strip().split())
                        if conf > 0:
                            confidences.append(conf)
                        valid_lines.append(str(text).strip())

        # Fallback: enhanced contrast if first pass yielded nothing
        if not valid_lines:
            enhanced_bgr = self._enhance_contrast(img_bgr)
            result, elapse_list = engine(enhanced_bgr)
            if result:
                for line in result:
                    if line and len(line) >= 2:
                        text = line[1]
                        conf = float(line[2]) if len(line) >= 3 and line[2] else 0.0
                        if text and str(text).strip():
                            extracted_words.extend(str(text).strip().split())
                            if conf > 0:
                                confidences.append(conf)
                            valid_lines.append(str(text).strip())

        avg_conf = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )
        full_text = "\n".join(valid_lines).strip()

        return {
            "text": full_text,
            "average_confidence": round(avg_conf, 2),
            "word_count": len(extracted_words),
            "lines": valid_lines,
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
