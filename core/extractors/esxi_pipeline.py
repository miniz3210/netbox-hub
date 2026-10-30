"""
ESXi analysis pipeline: pasted page TEXT first, OCR only to fill gaps, OCR alone when there is no text.

  analyze_esxi(text, get_ocr_records=None)
    1. text has switch content -> source "text"      (authoritative; OCR is NOT run unless the page itself reports a gap)
    2. text has fillable gaps and screenshots exist -> source "text+ocr" (OCR fills ONLY those gaps, tagged is_uncertain)
    3. no usable text, screenshots exist             -> source "ocr"       (the existing screenshot pipeline)
    4. nothing                                           -> source "none"
  `get_ocr_records` is a zero-argument callable returning OCR records; it is called lazily (OCR is slow), at most once.

Rules for the OCR top-up (so OCR can never make good data worse)
  * text records are never changed or removed by OCR
  * OCR only fills a gap the page reported (vmk_missing / nic_missing)
  * anything OCR adds is tagged source="ocr", is_uncertain=True, note=...
  * OCR vs text disagreements are reported as conflicts (text wins); OCR-only port groups are only suggested
  * switch names are matched by exact key after a deterministic suffix repair (dvSwitcha->dvSwitch0). No fuzzy matching.
"""
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .esxi_text import parse_vsphere_text_ex

_SUFFIX = re.compile(r"^(?P<stem>.*?[Ss]witch)(?P<suf>[0-9oOQDalI|]{1,3})$")
_SUF_MAP = str.maketrans("oOQDalI|", "00000111")


def _repair_switch(name: str) -> str:
    name = re.sub(r"[-_ ]*DVUplinks.*$", "", name or "", flags=re.I).strip()
    m = _SUFFIX.match(name)
    return (m.group("stem") + m.group("suf").translate(_SUF_MAP)) if m else name


def _k(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _dedupe_ocr(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The same switch is often visible in several overlapping screenshots - keep one copy of identical OCR records."""
    seen, out = set(), []
    for r in records:
        key = (_k(_repair_switch(r.get("switch", ""))), str(r.get("type", "")).lower(), str(r.get("interface", "")).lower(),
               _k(r.get("purpose", "")), str(r.get("ip", "")).strip())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def merge_ocr_into_text(text_records, gaps, ocr_records) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    merged = [dict(r) for r in text_records]
    rep: Dict[str, Any] = {"filled": [], "unresolved": [], "conflicts": [], "suggestions": [], "unmatched_switches": []}
    by_key = {_k(r["switch"]): r["switch"] for r in text_records}

    ocr_by_sw: Dict[str, List[Dict[str, Any]]] = {}
    unmatched = set()
    for r in _dedupe_ocr(ocr_records):
        sw = by_key.get(_k(_repair_switch(r.get("switch", ""))))
        if sw is None:
            unmatched.add(r.get("switch", ""))
        else:
            ocr_by_sw.setdefault(sw, []).append(r)
    rep["unmatched_switches"] = sorted(s for s in unmatched if s)

    def have(sw, iface):
        return any(m["switch"] == sw and m["interface"].lower() == iface.lower() for m in merged)

    for sw, recs in ocr_by_sw.items():
        for o in recs:
            if o["type"] != "VMkernel":
                continue
            for t in text_records:
                if t["switch"] == sw and t["type"] == "VMkernel" and t["interface"].lower() == o["interface"].lower() \
                        and t["ip"] and t["ip"] != o["ip"]:
                    rep["conflicts"].append(f"{sw} {o['interface']}: text says {t['ip']}, OCR read {o['ip']} -> kept text value.")

    for g in gaps:
        if not g.get("ocr_can_help"):
            rep["unresolved"].append(g["message"])
            continue
        sw, cands = g["switch"], ocr_by_sw.get(g["switch"], [])
        need = g["expected"] - g["found"]
        if g["kind"] == "vmk_missing":
            pool = [o for o in cands if o["type"] == "VMkernel" and o["interface"] and o["ip"]
                    and not any(m["switch"] == sw and m["interface"].lower() == o["interface"].lower() and m["ip"] for m in merged)]
            by_if: Dict[str, List[Dict[str, Any]]] = {}
            for o in pool:
                by_if.setdefault(o["interface"].lower(), []).append(o)
            pool = [v[0] for v in by_if.values() if len({x["ip"] for x in v}) == 1]      # screenshots disagree -> leave it unresolved
            same = [o for o in pool if _k(o["purpose"]) == _k(g["card"])]
            pick = same if same else (pool if len(pool) == need else [])
            if len(pick) >= need > 0:
                old = [m for m in merged if m["switch"] == sw and _k(m["purpose"]) == _k(g["card"]) and m["type"] in ("Port Group", "VMkernel") and not m["ip"]]
                vlan = old[0].get("vlan", "") if old else ""
                merged[:] = [m for m in merged if m not in old]
                for o in pick[:need]:
                    merged.append({"switch": sw, "interface": o["interface"], "type": "VMkernel", "ip": o["ip"], "purpose": g["card"],
                                "role": "None", "speed": "", "vlan": vlan, "source": "ocr", "is_uncertain": True,
                                "note": "vmk/IP filled by OCR (missing from pasted text) - verify"})
                    rep["filled"].append(f"{sw} / {g['card']}: {o['interface']} {o['ip']} (from OCR)")
            else:
                rep["unresolved"].append(g["message"] + " OCR could not supply it (not in the screenshot, or ambiguous).")

        elif g["kind"] == "nic_missing":
            pool = [o for o in cands if o["type"] == "Uplink" and o["interface"] not in ("No Uplink", "vmnic?", "") and not have(sw, o["interface"])]
            pool = list({o["interface"]: o for o in pool}.values())
            if 0 < need == len(pool):
                for o in pool:
                    merged.append({"switch": sw, "interface": o["interface"], "type": "Uplink", "ip": "", "purpose": "",
                                   "role": "Active Uplink", "speed": o.get("speed", ""), "vlan": "", "source": "ocr", "is_uncertain": True,
                                   "note": "vmnic filled by OCR (missing from pasted text) - verify"})
                    rep["filled"].append(f"{sw}: {o['interface']} (from OCR)")
                merged[:] = [m for m in merged if not (m["switch"] == sw and m["interface"] == "No Uplink")]
            else:
                rep["unresolved"].append(g["message"] + " OCR could not supply it (not in the screenshots, or ambiguous).")

    for sw, recs in ocr_by_sw.items():
        titles = {_k(m["purpose"]) for m in merged if m["switch"] == sw and m["purpose"]}
        for o in recs:
            if o["type"] in ("Port Group", "VMkernel") and o["purpose"] and _k(o["purpose"]) not in titles:
                rep["suggestions"].append(f"{sw}: OCR also saw {o['purpose']!r} - not in the pasted text (not added).")
                titles.add(_k(o["purpose"]))
    return merged, rep

def analyze_esxi(text: str, get_ocr_records: Optional[Callable[[], List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Return {"source","records","warnings","gaps","report","needs_screenshot"}. records are NOT reduced yet."""
    empty_rep = {"filled": [], "unresolved": [], "conflicts": [], "suggestions": [], "unmatched_switches": []}
    text = (text or "").strip()
    warnings: List[str] = []

    if text:
        p = parse_vsphere_text_ex(text)
        gaps = p["gaps"]
        fillable = [g for g in gaps if g.get("ocr_can_help")]
        if p["records"]:
            if fillable and get_ocr_records is not None:
                merged, rep = merge_ocr_into_text(p["records"], gaps, get_ocr_records() or [])
                return {"source": "text+ocr", "records": merged, "warnings": p["warnings"], "gaps": gaps,
                        "report": rep, "needs_screenshot": False}
            rep = dict(empty_rep, unresolved=[g["message"] for g in gaps])
            return {"source": "text", "records": p["records"], "warnings": p["warnings"], "gaps": gaps,
                    "report": rep, "needs_screenshot": bool(fillable)}
        warnings = p["warnings"] + (["Pasted text had no usable switch content."] if get_ocr_records is not None else [])
        if get_ocr_records is None:
            return {"source": "none", "records": [], "warnings": warnings or ["Pasted text had no usable switch content."],
                    "gaps": gaps, "report": dict(empty_rep, unresolved=[g["message"] for g in gaps]), "needs_screenshot": True}

    if get_ocr_records is not None:
        recs = get_ocr_records() or []
        if recs:
            return {"source": "ocr", "records": recs, "warnings": warnings, "gaps": [], "report": empty_rep, "needs_screenshot": False}
        return {"source": "none", "records": [], "warnings": warnings + ["OCR found no topology in the screenshots."],
                "gaps": [], "report": empty_rep, "needs_screenshot": False}

    return {"source": "none", "records": [], "warnings": ["Nothing to analyse: paste the vSphere page text and/or upload screenshots."],
            "gaps": [], "report": empty_rep, "needs_screenshot": False}
