"""
VMware ESXi & vDS Universal High-Recall Extractor (v4.8.31 - space-loss tolerant)

Key differences vs 4.8.30
  * Tolerates OCR that drops spaces ("ADDNETWORKING", "vmnic210000Full", "TestVLAN10").
  * Switch names come from the header *token*, never from the whole header line.
  * Empty band == collapsed switch -> no "No Uplink" placeholder is emitted.
  * Resolution-agnostic (token boxes are rescaled to a reference text height).
  * NIC extraction works even if the "Physical Adapters" anchor is missed.
"""

import re
from typing import List, Dict, Any, Optional, Tuple
from .base import BaseTopologyExtractor, get_bounding_box

REF_TEXT_H = 16.0
SPEEDS = r"(?:100000|50000|40000|25000|10000|5000|2500|1000|100)"
SPEED_UNIT = r"(?:Gbit/s|Mbit/s|Full|Half)"

# --------------------------------------------------------------------------
# STAGE 0: GEOMETRY + TEXT HELPERS
# --------------------------------------------------------------------------

def normalise_scale(tokens: List[Dict[str, Any]], ref_h: float = REF_TEXT_H) -> List[Dict[str, Any]]:
    """Rescale boxes so median text height == ref_h; all pixel thresholds then mean the same thing."""
    hs = sorted(h for h in (get_bounding_box(t)[3] - get_bounding_box(t)[1] for t in tokens) if h > 0)
    if not hs:
        return tokens
    k = ref_h / hs[len(hs) // 2]
    out = []
    for t in tokens:
        x0, y0, x1, y1 = get_bounding_box(t)
        c = dict(t)
        c["box"] = [x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k]
        out.append(c)
    return out


def apply_token_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    # OCR 'o' for '0' ONLY as a switch suffix: dvSwitcho -> dvSwitch0, vSwitchO1 -> vSwitch01,
    # but NOT vSwitchOffice (lookahead requires digits-then-word-boundary).
    s = re.sub(r"(?i)(?<=switch)[o](?=\d*\b)", "0", s)
    for rule in normalizations:
        pat, rep = rule.get("pattern"), rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


def heal_ipv4(raw_str: str) -> Optional[str]:
    """Repair common OCR damage without gluing two separate numbers together."""
    s = re.sub(r"\s*\.\s*", ".", raw_str.strip())              # '10 . 27.1.5' -> '10.27.1.5'
    s = re.sub(r"(?<=[\d.])[Oo](?=[\d.])", "0", s)             # 10.27.1O.5
    s = re.sub(r"(?<=[\d.])[lI|](?=[\d.])", "1", s)
    m = re.search(r"(?<!\d)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)", s)
    if not m:
        return None
    parts = [int(p) for p in m.groups()]
    return ".".join(str(p) for p in parts) if all(0 <= p <= 255 for p in parts) else None


def restore_title_spaces(text: str) -> str:
    """
    Conservative camel-case split for port-group titles when OCR dropped the spaces.
    ManagementNetwork -> Management Network | iSCSIVeeam -> iSCSI Veeam | VMNetwork -> VM Network
    Digit boundaries are NOT guessed (TestVLAN10 -> Test VLAN10) because real names are inconsistent.
    Leaves vMotion / iSCSI01 / vSwitch0 untouched (left part must be >= 2 lowercase chars).
    Only call this on port-group titles, never on switch names (dvSwitch would break).
    """
    if " " in text.strip():
        return text
    s = re.sub(r"(?<=[a-z]{2})(?=[A-Z])", " ", text)           # lower->Upper
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z]{2})", " ", s)         # UPPERRun -> Capitalised  (VMNetwork)
    return re.sub(r"\s+", " ", s).strip()


def cluster_lines(tokens: List[Dict[str, Any]], y_thresh: float = 12.0, max_x_gap: Optional[float] = 60.0) -> List[Dict[str, Any]]:
    if not tokens:
        return []
    bb = get_bounding_box
    sorted_tokens = sorted(tokens, key=lambda t: (bb(t)[1], bb(t)[0]))
    rows: List[List[Dict[str, Any]]] = []
    for t in sorted_tokens:
        mid = (bb(t)[1] + bb(t)[3]) / 2.0
        for r in rows:
            if abs(mid - sum((bb(x)[1] + bb(x)[3]) / 2.0 for x in r) / len(r)) <= y_thresh:
                r.append(t)
                break
        else:
            rows.append([t])

    lines: List[Dict[str, Any]] = []
    for r in rows:
        r.sort(key=lambda x: bb(x)[0])
        if max_x_gap is None:
            segments = [r]
        else:
            segments, curr = [], [r[0]]
            for tok in r[1:]:
                if bb(tok)[0] - bb(curr[-1])[2] > max_x_gap:
                    segments.append(curr); curr = [tok]
                else:
                    curr.append(tok)
            segments.append(curr)
        for seg in segments:
            x0 = min(bb(x)[0] for x in seg); y0 = min(bb(x)[1] for x in seg)
            x1 = max(bb(x)[2] for x in seg); y1 = max(bb(x)[3] for x in seg)
            txt = " ".join(str(x.get("text", "")).strip() for x in seg if str(x.get("text", "")).strip())
            lines.append({"text": txt, "tokens": seg, "box": [x0, y0, x1 - x0, y1 - y0],
                          "x": x0, "y": y0, "y_bottom": y1})
    lines.sort(key=lambda l: (l["y"], l["x"]))
    return lines


# --------------------------------------------------------------------------
# STAGE 2: SWITCH HEADER DETECTION  (token-based)
# --------------------------------------------------------------------------

_SW_KW = re.compile(r"Sw[i1l!t]+ch\s*[:;]", re.I)
# Cuts that work with OR without spaces: ADDNETWORKING, ADD NETWORKING, MANAGEPHYSKALADAPTERS ...
_ACTION_CUT = re.compile(
    r"(?:\s*\|.*$|\s*\.{2,}.*$|\s*….*$"
    r"|\s*ADD\s*NET\w*.*$|\s+EDIT\b.*$|\s*MANAGE\s*PHYS\w*.*$|\s+ACTIONS\b.*$|\s+REFRESH\b.*$)",
    re.I,
)


def _clean_switch_name(raw: str, normalizations) -> str:
    s = _ACTION_CUT.sub("", raw.strip())
    s = re.sub(r"^[>\(\)\[\]\{\}<\-\.\:\;\|\+\*\s]+", "", s)
    s = re.sub(r"[\s\.\u2026\u00d7<>|:;]+$", "", s)            # keep ) ] - : names like "dvSwitch (Prod)"
    return apply_token_normalizations(s, normalizations)


def detect_switches(lines: List[Dict[str, Any]], normalizations) -> List[Dict[str, Any]]:
    switches = []
    for l in lines:
        txt = l["text"].strip()
        if re.search(r"DVUplink", txt, re.I) or (re.search(r"Virtual\s*switches", txt, re.I) and not _SW_KW.search(txt)):
            continue

        raw_name, kw_tok = None, None
        toks = l["tokens"]
        for i, tok in enumerate(toks):
            ttxt = str(tok.get("text", "")).strip()
            m = _SW_KW.search(ttxt)
            if not m:
                continue
            kw_tok = tok
            tail = ttxt[m.end():].strip()
            if not tail and i + 1 < len(toks):                  # 'Standard Switch:' | 'vSwitch0'
                tail = str(toks[i + 1].get("text", "")).strip()
            raw_name = tail
            break
        if raw_name is None:
            m = re.search(r"Sw[i1l!t]+ch\s*[:;.]\s*(.+)", txt, re.I)
            if not m:
                continue
            raw_name = m.group(1)
            kw_tok = next((t for t in toks if re.search(r"Sw[i1l!t]+ch", str(t.get("text", "")), re.I)), toks[0])

        sw_name = _clean_switch_name(raw_name, normalizations)
        if len(sw_name) < 2:
            continue
        if any(abs(s["y"] - l["y"]) <= 18.0 and s["name"] == sw_name for s in switches):
            continue
        switches.append({
            "name": sw_name, "y": l["y"], "y_bottom": l["y_bottom"],
            "x": max(0.0, get_bounding_box(kw_tok)[0] - 20.0),
            "collapsed": bool(re.search(r"^[>▶\+]", txt)),
        })

    switches.sort(key=lambda s: s["y"])
    for i, sw in enumerate(switches):
        sw["y_start"] = sw["y"]
        sw["y_end"] = switches[i + 1]["y"] if i + 1 < len(switches) else float("inf")
    return switches


# --------------------------------------------------------------------------
# STAGE 3: PHYSICAL UPLINKS  (tolerates 'vmnic210000Full')
# --------------------------------------------------------------------------

# index is 1-2 chars (lazy) so that '...c2' + '10000' splits correctly; O/l/I repaired to 0/1/1
_NIC_RE = re.compile(
    r"(?P<st>[xX!\*\+]\s*)?\bvmn[a-z0-9]{1,2}?\s*(?P<idx>[0-9OolI|]{1,2}?)"
    r"(?:\s*(?P<spd>" + SPEEDS + r")\s*(?P<unit>" + SPEED_UNIT + r")?)?(?![0-9])",
    re.I,
)


def _sentinel(sw_name: str) -> Dict[str, Any]:
    return {"switch": sw_name, "interface": "No Uplink", "type": "Uplink",
            "ip": "", "purpose": "", "role": "None", "speed": ""}


def extract_physical_uplinks(tokens, sw_name, normalizations, emit_sentinel: bool = True):
    found, seen = [], set()
    for pline in cluster_lines(tokens, y_thresh=8.0, max_x_gap=80.0):
        ptxt = pline["text"].strip()
        if re.search(r"\(0\s*NIC|No\s*physical", ptxt, re.I):
            continue
        for m in _NIC_RE.finditer(ptxt):
            digit = m.group("idx").translate(str.maketrans("OolI|", "00111"))
            if not digit.isdigit():
                continue
            nic = apply_token_normalizations(f"vmnic{digit}", normalizations)
            if nic in seen:
                continue
            seen.add(nic)
            standby = bool(re.search(r"[xX!]", m.group("st") or "")) or \
                any(re.fullmatch(r"[xX!]", str(t.get("text", "")).strip()) for t in pline["tokens"])
            spd = (m.group("spd") or "") + (" " + m.group("unit") if m.group("unit") else "")
            found.append({"switch": sw_name, "interface": nic, "type": "Uplink", "ip": "",
                          "purpose": "", "role": "Standby Uplink" if standby else "Active Uplink",
                          "speed": spd.strip()})
    if found:
        return found
    return [_sentinel(sw_name)] if emit_sentinel else []


# --------------------------------------------------------------------------
# STAGE 4: LOGICAL CARDS & VMKERNEL ANCHORING
# --------------------------------------------------------------------------

_VLAN_ROW = re.compile(r"(?:V[L1I|]?A?N\s*[I1l|]D\s*[:;.]|(?<!\w)ID\s*[:;]\s*(?:\d+|\-+))", re.I)


def extract_logical_cards(tokens, sw_name, normalizations, y_start, restore_spaces: bool = True):
    if not tokens:
        return []
    lines = cluster_lines(tokens, y_thresh=10.0, max_x_gap=65.0)

    vmk_entries = []
    for l in lines:
        txt = re.sub(r"[^\x20-\x7E]+", " ", l["text"]).strip()
        m = re.search(r"\b(vm[kldo0-9a-zA-Z]{1,4})\s*[:;\s]\s*([0-9OolI|\.\s]{6,})", txt, re.I)
        if not m:
            continue
        ip = heal_ipv4(m.group(2))
        if not ip:
            continue
        raw_v = m.group(1).lower()
        d = re.search(r"(\d+)", raw_v)
        vmk = f"vmk{d.group(1)}" if d else ("vmk1" if re.search(r"[dli1]$", raw_v) else "vmk0")
        vmk_entries.append({"vmk": vmk, "ip": ip, "y": l["y"]})

    vlan_rows = [l for l in lines if _VLAN_ROW.search(l["text"])]

    cards = []
    for i, v_row in enumerate(vlan_rows):
        vy = v_row["y"]
        prev = vlan_rows[i - 1]["y"] if i else y_start
        best = None
        for l in lines:
            ly = l["y"]
            if not (prev < ly < vy and 4.0 <= vy - ly <= 58.0):
                continue
            txt = re.sub(r"[^\x20-\x7E]+", " ", l["text"]).strip()
            if re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\b\s*[:;]|ADD\s*NETWORKING|MANAGE\s*PHYS", txt, re.I):
                continue
            if re.search(r"(?:vmk[eao]rn|virt\s*ual\s*mach|machines?\s*\(|\bvmk\s*[a-zA-Z0-9]+\s*[:;])", txt, re.I):
                continue
            c = re.sub(r"^[>\(\)\[\]\{\}<\-\.\:\;\|\+\*\s]+", "", txt).strip()
            c = re.sub(r"(?:\s*\.{2,}|\s*\u2026|[\s\.\u2026\u00d7<>|:;])+$", "", c).strip()
            c = apply_token_normalizations(c, normalizations)
            if restore_spaces:
                c = restore_title_spaces(c)
            if len(c) >= 2 and (best is None or vy - ly < best[0]):
                best = (vy - ly, c, ly)
        if best:
            cards.append({"title": best[1], "y": best[2]})

    records, assigned = [], set()
    for v in vmk_entries:
        above = [c for c in cards if c["y"] <= v["y"]]
        if above:
            purpose = max(above, key=lambda c: c["y"])["title"]    # nearest card title above
            assigned.add(purpose)
        else:
            purpose = v["vmk"]
        records.append({"switch": sw_name, "interface": v["vmk"], "type": "VMkernel",
                        "ip": v["ip"], "purpose": purpose, "role": "None", "speed": ""})
    for c in cards:
        if c["title"] not in assigned:
            assigned.add(c["title"])
            records.append({"switch": sw_name, "interface": "", "type": "Port Group",
                            "ip": "", "purpose": c["title"], "role": "None", "speed": ""})
    return records


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

_PHYS_ANCHOR = re.compile(r"(?:Physical\s*Adapters|DVUplink|NIC\s*Adapters|\bvmn[a-z0-9]{1,2}\s*\d)", re.I)


class EsxiTopologyExtractor(BaseTopologyExtractor):
    def extract(self, raw_tokens, image_width: int = 1920, image_height: int = 1080,
                plat_cfg: Optional[Dict[str, Any]] = None):
        if not raw_tokens:
            return []
        cfg = plat_cfg or {}
        norms = cfg.get("token_normalizations", [])
        restore = cfg.get("restore_title_spaces", True)

        tokens = []
        for t in normalise_scale(raw_tokens):
            c = dict(t)
            c["text"] = apply_token_normalizations(str(t.get("text", "")).strip(), norms)
            tokens.append(c)

        switches = detect_switches(cluster_lines(tokens, y_thresh=14.0, max_x_gap=None), norms)
        if not switches:
            return []
        sidebar_x = max(0.0, min(s["x"] for s in switches) - 15.0)
        bb = get_bounding_box
        noise_tok = {">", "▶", "v", "▼", "...", "|", "…"}

        records: List[Dict[str, Any]] = []
        for sw in switches:
            name = sw["name"]
            band = [t for t in tokens
                    if bb(t)[0] >= sidebar_x
                    and (bb(t)[1] + bb(t)[3]) / 2.0 > sw["y_bottom"] + 2.0      # below the header row
                    and (bb(t)[1] + bb(t)[3]) / 2.0 < sw["y_end"] - 3.0
                    and t["text"] not in noise_tok]
            if not band:
                continue          # collapsed switch: nothing to extract, and NO placeholder record

            anchor = min((bb(t)[0] for t in band if _PHYS_ANCHOR.search(t["text"])), default=None)
            if anchor is not None and anchor > sidebar_x + 150.0:
                logical = [t for t in band if bb(t)[0] < anchor - 10.0]
                physical = [t for t in band if bb(t)[0] >= anchor - 15.0]
            else:
                logical, physical = band, []

            ups = extract_physical_uplinks(physical, name, norms, emit_sentinel=False) if physical else []
            if not ups:           # anchor missed or column split wrong -> scan whole band for vmnic
                ups = extract_physical_uplinks(band, name, norms, emit_sentinel=False)
            records.extend(ups or [_sentinel(name)])
            records.extend(extract_logical_cards(logical, name, norms, sw["y_start"], restore))

        with_data = {r["switch"] for r in records if r["interface"] != "No Uplink" or r["purpose"]}
        return [r for r in records
                if not (r["switch"] in with_data and r["interface"] == "No Uplink" and not r["purpose"])]
