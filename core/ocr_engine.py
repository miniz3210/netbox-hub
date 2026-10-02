"""
Local OCR Engine for Topology & Table Extraction.

Executes local native Tesseract OCR on ARM64 CPU without GPU dependencies.
Computes word-level extraction confidence and applies quality gate evaluation.
"""

import io
import re
from typing import List, Dict, Any, Tuple
from PIL import Image
import pytesseract


class LocalOCREngine:
    def __init__(self, min_confidence_threshold: float = 40.0):
        self.min_confidence_threshold = min_confidence_threshold

    @staticmethod
    def _load_image(image_input: Any) -> Image.Image:
        if isinstance(image_input, Image.Image):
            return image_input
        if hasattr(image_input, "read"):
            data = image_input.read()
            if hasattr(image_input, "seek"):
                image_input.seek(0)
            return Image.open(io.BytesIO(data))
        if isinstance(image_input, bytes):
            return Image.open(io.BytesIO(image_input))
        if isinstance(image_input, str):
            return Image.open(image_input)
        raise ValueError("Unsupported image format provided to LocalOCREngine")

    def extract_text_with_metadata(
        self, image_input: Any
    ) -> Dict[str, Any]:
        """
        Extract text, line-level bounding segments, and overall confidence metrics.
        """
        img = self._load_image(image_input)
        # Convert image to RGB if palette/RGBA
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        ocr_data = pytesseract.image_to_data(
            img, output_type=pytesseract.Output.DICT
        )

        extracted_words: List[str] = []
        confidences: List[float] = []
        valid_lines: List[str] = []

        current_line: List[str] = []
        n_boxes = len(ocr_data.get("text", []))

        for i in range(n_boxes):
            word = str(ocr_data["text"][i]).strip()
            conf = float(ocr_data["conf"][i])

            if conf > 0 and word:
                extracted_words.append(word)
                confidences.append(conf)
                current_line.append(word)

            if ocr_data["word_num"][i] == 0 or i == n_boxes - 1:
                if current_line:
                    valid_lines.append(" ".join(current_line))
                    current_line = []

        avg_conf = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )
        full_text = pytesseract.image_to_string(img).strip()

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