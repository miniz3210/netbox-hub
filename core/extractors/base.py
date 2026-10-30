from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple


def get_bounding_box(token_or_box: Any) -> Tuple[float, float, float, float]:
    """
    Polymorphic coordinate helper returning (x_min, y_min, x_max, y_max).
    Tolerates dict with 'box', flat 4-float list [x, y, w, h], or polygon points [[x, y], ...].
    """
    if isinstance(token_or_box, dict):
        b = token_or_box.get("box")
    else:
        b = token_or_box

    if not b:
        return 0.0, 0.0, 0.0, 0.0

    if isinstance(b[0], (list, tuple)):
        xs = [float(p[0]) for p in b]
        ys = [float(p[1]) for p in b]
        return min(xs), min(ys), max(xs), max(ys)

    if len(b) >= 4:
        x0 = float(b[0])
        y0 = float(b[1])
        w = float(b[2])
        h = float(b[3])
        return x0, y0, x0 + w, y0 + h

    return 0.0, 0.0, 0.0, 0.0


class BaseTopologyExtractor(ABC):
    @abstractmethod
    def extract(
        self,
        raw_tokens: List[Dict[str, Any]],
        image_width: int,
        image_height: int,
        plat_cfg: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Must return list of standardized topology records matching the exact 7-key contract:
        [{"switch": str, "interface": str, "type": str, "ip": str, "purpose": str, "role": str, "speed": str}]
        """
        pass
