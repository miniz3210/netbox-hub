"""
VMware ESXi & vDS Modular Topology Extractor (v4.8.26)
Pipeline:
- Brick 1: Pre-Normalization (Config-driven string cleaning at raw ingest)
- Brick 2: Layout Segmentation (Sidebar exclusion & Switch horizontal bands)
- Brick 3: Physical Uplink Extractor (Broad prefix tolerance: vmnici1 -> vmnic1)
- Brick 4: Logical Card-Top Extractor (VLAN ID anchor strictly pairs with top title)
"""

import re
from typing import List, Dict, Any, Optional
from .base import BaseTopologyExtractor, get_bounding_box


# --- LEGO BRICK 1: PRE-NORMALIZATION & TEXT HELPERS ---

def apply_token_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    for rule in normalizations:
        pat = rule.get("pattern")
        rep = rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


def heal_ipv4(raw_str: str) -> Optional[str]:
    clean = re.sub(r"(?<=[0-9\.])\s+(?=[0-9\.])", "", raw_str)
    m = re.search(r"(\d[\d\.]{6,16}\d)", clean)
    if not m:
        return None
    cand = m.group(1)
    parts = [p for p in cand.split(".") if p != ""]
    if len(parts) == 3:
        new_parts = []
        for p in parts:
            if len(p) in (4, 5, 6) and (len(new_parts) + (3 - len(new_parts))) <= 4:
                p1, p2 = p[:3], p[3:] if int(p[:3]) <= 255 else (p[:2], p[2:])
                new_parts.extend([p1, p2])
            else:
                new_parts.append(p)
        parts = new_parts
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return ".".join(str(int(p)) for p in parts)
    return None


def cluster_lines(tokens: List[Dict[str, Any]], y_thresh: float = 10.0, max_x_gap: Optional[float] = 60.0) -> List[Dict[str, Any]]:
    if not tokens:
        return []

    sorted_tokens = sorted(tokens, key=lambda t: (get_bounding_box(t)[1], get_bounding_box(t)[0]))
    raw_rows: List[List[Dict[str, Any]]] = []

    for t in sorted_tokens:
        _, y0, _, y1 = get_bounding_box(t)
        mid_y = (y0 + y1) / 2.0
        placed = False
        for row in raw_rows:
            row_mid_y = sum((get_bounding_box(x)[1] + get_bounding_box(x)[3]) / 2.0 for x in row) / len(row)
            if abs(mid_y - row_mid_y) <= y_thresh:
                row.append(t)
                placed = True
                break
        if not placed:
            raw_rows.append([t])

    lines: List[Dict[str, Any]] = []
    for row in raw_rows:
        row.sort(key=lambda x: get_bounding_box(x)[0])
        segments = []
        if max_x_gap is None:
            segments = [row]
        else:
            curr = [row[0]]
            for tok in row[1:]:
                if (get_bounding_box(tok)[0] - get_bounding_box(curr[-1])[2]) > max_x_gap:
                    segments.append(curr)
                    curr = [tok]
                else:
                    curr.append(tok)
            segments.append(curr)

        for seg in segments:
            min_x = min(get_bounding_box(x)[0] for x in seg)
            min_y = min(get_bounding_box(x)[1] for x in seg)
            max_x = max(get_bounding_box(x)[2] for x in seg)
            max_y = max(get_bounding_box(x)[3] for x in seg)
            txt = " ".join(str(x.get("text", "")).strip() for x in seg if str(x.get("text", "")).strip())
            lines.append({
                "text": txt,
                "tokens": seg,
                "box": [min_x, min_y, max_x - min_x, max_y - min_y],
                "x": min_x,
                "y": min_y
            })

    lines.sort(key=lambda l: (l["y"], l["x"]))
    return lines


# --- LEGO BRICK 2: SWITCH HEADER & LAYOUT SEGMENTATION ---

def detect_switches(lines: List[Dict[str, Any]], normalizations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    switches = []
    for line in lines:
        txt = line["text"]
        if re.search(r"DVUplink|Virtual\s*switches", txt, re.I):
            continue

        has_kw = bool(re.search(r"(?:Standard|Distributed|Dist[a-z]*|Stand[a-z]*)\s*Sw[a-z]*|\bSw[i1l!t]+ch\b\s*[:;]", txt, re.I))
        if not has_kw:
            continue

        parts = re.split(r"(?:Sw[i1l!t]+ch|Bridge)\s*[:;]?\s*", txt, maxsplit=1, flags=re.I)
        if len(parts) < 2:
            continue

        candidate = re.split(r"(?:\s+\||\s+ADD\b|\s+EDIT\b|\s+MANAGE\b|\s+ACTIONS\b|\||\.{2,})", parts[1].strip(), maxsplit=1, flags=re.I)[0].strip()
        m = re.match(r"^([A-Za-z0-9_\-\.]+)", candidate)
        if not m:
            continue

        sw_name = apply_token_normalizations(m.group(1).strip(), normalizations)
        if len(sw_name) < 2:
            continue

        kw_x0 = line["x"]
        for t in line["tokens"]:
            ttxt = str(t.get("text", "")).strip()
            if re.search(r"(?:Standard|Distributed|Dist[a-z]*|Sw[i1l!t]+ch|Bridge)", ttxt, re.I):
                kw_x0 = max(0.0, get_bounding_box(t)[0] - 20.0)
                break

        if not any(abs(s["y_top"] - line["y"]) <= 18.0 and s["name"] == sw_name for s in switches):
            switches.append({
                "name": sw_name,
                "y_top": line["y"],
                "x_left": kw_x0
            })

    switches.sort(key=lambda s: s["y_top"])
    for idx, s in enumerate(switches):
        s["y_start"] = s["y_top"]
        s["y_end"] = switches[idx + 1]["y_top"] if (idx + 1) < len(switches) else float("inf")
    return switches


# --- LEGO BRICK 3: PHYSICAL UPLINK EXTRACTION ---

def extract_physical_uplinks(tokens: List[Dict[str, Any]], sw_name: str, normalizations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not tokens:
        return [{
            "switch": sw_name, "interface": "No Uplink", "type": "Uplink",
            "ip": "", "purpose": "", "role": "None", "speed": ""
        }]

    phys_lines = cluster_lines(tokens, y_thresh=8.0, max_x_gap=80.0)
    uplinks = []
    seen = set()

    for pline in phys_lines:
        txt = pline["text"].strip()
        if re.search(r"\(0\s*NIC\s*Adapters\)|No\s*physical\s*network", txt, re.I):
            continue

        txt_clean = re.sub(r"\b(?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?\b", " ", txt, flags=re.I)

        # Forgiving match: handles vmnic1, vmnici1, vmnlc2, vmnkc3, vnnic0
        for m in re.finditer(r"([xX!\*\+]\s*)?\b(?:v\s*[mn][a-z0-9]{1,4})\s*([0-9lI|])\b", txt_clean, re.I):
            status = m.group(1) or ""
            raw_digit = m.group(2)
            digit = "1" if raw_digit in ("l", "I", "|") else raw_digit
            nic_name = apply_token_normalizations(f"vmnic{digit}", normalizations)

            if nic_name in seen:
                continue
            seen.add(nic_name)

            is_standby = bool(re.search(r"[xX!]", status)) or bool(re.search(r"^[xX!]\b|\bStandby\b", txt, re.I))
            if not is_standby:
                for t in pline["tokens"]:
                    if re.match(r"^[xX!]$", str(t.get("text", "")).strip()):
                        is_standby = True
                        break

            sm = re.search(r"\b((?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?|\d+\s*(?:Gbit/s|Mbit/s))\b", txt, re.I)
            speed = sm.group(1).strip() if sm else ""

            uplinks.append({
                "switch": sw_name,
                "interface": nic_name,
                "type": "Uplink",
                "ip": "",
                "purpose": "",
                "role": "Standby Uplink" if is_standby else "Active Uplink",
                "speed": speed,
                "y": pline["y"]
            })

    uplinks.sort(key=lambda u: u["y"])
    for u in uplinks:
        u.pop("y", None)

    return uplinks if uplinks else [{
        "switch": sw_name, "interface": "No Uplink", "type": "Uplink",
        "ip": "", "purpose": "", "role": "None", "speed": ""
    }]


# --- LEGO BRICK 4: LOGICAL PORT GROUP & VMKERNEL CARD EXTRACTION ---

def extract_logical_cards(tokens: List[Dict[str, Any]], sw_name: str, normalizations: List[Dict[str, str]], y_start: float) -> List[Dict[str, Any]]:
    if not tokens:
        return []

    lines = cluster_lines(tokens, y_thresh=10.0, max_x_gap=65.0)
    records = []

    # 1. VMkernel entries
    vmk_entries = []
    for line in lines:
        txt = re.sub(r"[^\x20-\x7E]+", " ", line["text"]).strip()
        m = re.search(r"\b(vmk\s*[a-zA-Z0-9]{1,2})\s*[:;\s]\s*([0-9][0-9\.\s]{6,})", txt, re.I)
        if m:
            ip = heal_ipv4(m.group(2))
            if ip:
                raw_v = re.sub(r"\s+", "", m.group(1)).lower()
                suffix = raw_v[3:]
                clean_v = "vmk0" if suffix in ("o", "0") else f"vmk{suffix}" if suffix.isdigit() else "vmk1"
                vmk_entries.append({"vmk": clean_v, "ip": ip, "y": line["y"]})

    # 2. Card-Top Title Invariant (Anchored strictly by VLAN ID row)
    vlan_rows = [l for l in lines if re.search(r"(?:V[L1I\|]?A?N\s*[I1l\|]?D|(?<!\w)ID\s*[:;]\s*(?:\d+|\-+))", l["text"], re.I)]
    cards = []

    for v_idx, v_row in enumerate(vlan_rows):
        vy = v_row["y"]
        prev_bound = vlan_rows[v_idx - 1]["y"] if v_idx > 0 else y_start

        candidates = []
        for l in lines:
            ly = l["y"]
            if not (prev_bound < ly < vy and 4.0 <= (vy - ly) <= 52.0):
                continue
            txt = re.sub(r"[^\x20-\x7E]+", " ", l["text"]).strip()
            if re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\b\s*[:;]|ADD\s*NETWORKING|MANAGE\s*PHYS", txt, re.I):
                continue
            if re.search(r"(?:vmk[eao]rn|virt\s*ual\s*mach|machines?\s*\(|\bvmk\s*[a-zA-Z0-9]+\s*[:;])", txt, re.I):
                continue

            cleaned = re.sub(r"^(?:[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*]+|[vV]\s+)\s*", "", txt).strip()
            cleaned = re.sub(r"[\s\.\×\<\>\(\)\[\]\{\}\|\-\:\;]+$", "", cleaned).strip()
            cleaned = apply_token_normalizations(cleaned, normalizations)

            if len(cleaned) > 2:
                candidates.append((vy - ly, cleaned, ly))

        if candidates:
            candidates.sort(key=lambda c: c[0])
            cards.append({"title": candidates[0][1], "y": candidates[0][2]})

    # 3. Assemble Port Groups & bind VMkernels
    assigned_cards = set()
    for v in vmk_entries:
        above = [c for c in cards if 0 <= (v["y"] - c["y"]) <= 160.0]
        if above:
            above.sort(key=lambda c: v["y"] - c["y"])
            purpose = above[0]["title"]
            assigned_cards.add(purpose)
        else:
            purpose = "Management Network" if v["vmk"] == "vmk0" else v["vmk"]

        records.append({
            "switch": sw_name, "interface": v["vmk"], "type": "VMkernel",
            "ip": v["ip"], "purpose": purpose, "role": "None", "speed": ""
        })

    for c in cards:
        if c["title"] not in assigned_cards:
            assigned_cards.add(c["title"])
            records.append({
                "switch": sw_name, "interface": "", "type": "Port Group",
                "ip": "", "purpose": c["title"], "role": "None", "speed": ""
            })

    return records


# --- MAIN EXTRACTOR CLASS ---

class EsxiTopologyExtractor(BaseTopologyExtractor):
    def extract(
        self, 
        raw_tokens: List[Dict[str, Any]], 
        image_width: int = 1920, 
        image_height: int = 1080, 
        plat_cfg: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not raw_tokens:
            return []

        cfg = plat_cfg or {}
        normalizations = cfg.get("token_normalizations", [])

        # Stage 1: Pre-Normalize all raw tokens before geometric processing
        clean_tokens = []
        for t in raw_tokens:
            tok_copy = dict(t)
            tok_copy["text"] = apply_token_normalizations(str(t.get("text", "")).strip(), normalizations)
            clean_tokens.append(tok_copy)

        # Stage 2: Layout Segmentation
        header_lines = cluster_lines(clean_tokens, y_thresh=14.0, max_x_gap=45.0)
        switches = detect_switches(header_lines, normalizations)
        if not switches:
            return []

        sidebar_cutoff_x = max(0.0, min(s["x_left"] for s in switches) - 10.0)
        records: List[Dict[str, Any]] = []

        # Process each Switch horizontal slice
        for sw in switches:
            band_tokens = [
                t for t in clean_tokens
                if get_bounding_box(t)[0] >= sidebar_cutoff_x
                and (sw["y_start"] + 14.0 <= get_bounding_box(t)[1] < sw["y_end"] - 6.0)
                and t["text"] not in (">", "▶", "v", "▼", "...", "|")
            ]

            # Split Logical vs Physical Columns
            logical_card_x = [
                get_bounding_box(t)[0] for t in band_tokens
                if re.search(r"(?:V[L1I]AN|Virt\s*ual\s*Mach|VMk[a-z]*\s*Port|vmk\s*[a-zA-Z0-9])", t["text"], re.I)
            ]
            min_phys_threshold = (max(logical_card_x) + 90.0) if logical_card_x else (sidebar_cutoff_x + 260.0)

            phys_anchor_x = None
            for t in band_tokens:
                if get_bounding_box(t)[0] >= min_phys_threshold and re.search(r"(?:Physical\s*Adapters|DVUplink|NIC\s*Adapters|vm[a-z0-9]+|No\s*phys)", t["text"], re.I):
                    if phys_anchor_x is None or get_bounding_box(t)[0] < phys_anchor_x:
                        phys_anchor_x = get_bounding_box(t)[0]

            if phys_anchor_x is not None:
                logical_toks = [t for t in band_tokens if get_bounding_box(t)[0] < (phys_anchor_x - 12.0)]
                physical_toks = [t for t in band_tokens if get_bounding_box(t)[0] >= (phys_anchor_x - 15.0)]
            else:
                logical_toks = band_tokens
                physical_toks = []

            # Stage 3: Extract Physical Uplinks
            records.extend(extract_physical_uplinks(physical_toks, sw["name"], normalizations))

            # Stage 4: Extract Logical Cards
            records.extend(extract_logical_cards(logical_toks, sw["name"], normalizations, sw["y_start"]))

        return records
