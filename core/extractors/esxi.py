"""
VMware ESXi & vDS Universal High-Recall Extractor (v4.8.30)
Standardized, Deterministic, Zero-Dropping Entity Pipeline.
"""

import re
from typing import List, Dict, Any, Optional
from .base import BaseTopologyExtractor, get_bounding_box


# --- STAGE 1: TEXT HELPERS & NORMALIZATION ---

def apply_token_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    s = re.sub(r"(?i)\bvSwitch[oO](\d*)\b", r"vSwitch0\1", s)
    for rule in normalizations:
        pat = rule.get("pattern")
        rep = rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


def heal_ipv4(raw_str: str) -> Optional[str]:
    clean = re.sub(r"(?<=[0-9\.])\s+(?=[0-9\.])", "", raw_str)
    m = re.search(r"(\d[\d\.]{5,16}\d)", clean)
    if not m:
        return None
    cand = m.group(1)
    parts = [p for p in cand.split(".") if p != ""]
    if len(parts) == 3:
        new_parts = []
        for p in parts:
            if len(p) in (4, 5, 6) and (len(new_parts) + (3 - len(new_parts))) <= 4:
                p1, p2 = (p[:3], p[3:]) if int(p[:3]) <= 255 else (p[:2], p[2:])
                new_parts.extend([p1, p2])
            else:
                new_parts.append(p)
        parts = new_parts
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return ".".join(str(int(p)) for p in parts)
    return None


def cluster_lines(tokens: List[Dict[str, Any]], y_thresh: float = 12.0, max_x_gap: Optional[float] = 60.0) -> List[Dict[str, Any]]:
    if not tokens:
        return []
    sorted_tokens = sorted(tokens, key=lambda t: (get_bounding_box(t)[1], get_bounding_box(t)[0]))
    rows: List[List[Dict[str, Any]]] = []

    for t in sorted_tokens:
        mid_y = (get_bounding_box(t)[1] + get_bounding_box(t)[3]) / 2.0
        placed = False
        for r in rows:
            r_mid_y = sum((get_bounding_box(x)[1] + get_bounding_box(x)[3]) / 2.0 for x in r) / len(r)
            if abs(mid_y - r_mid_y) <= y_thresh:
                r.append(t)
                placed = True
                break
        if not placed:
            rows.append([t])

    lines: List[Dict[str, Any]] = []
    for r in rows:
        r.sort(key=lambda x: get_bounding_box(x)[0])
        segments = []
        if max_x_gap is None:
            segments = [r]
        else:
            curr = [r[0]]
            for tok in r[1:]:
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


# --- STAGE 2: SWITCH HEADER DETECTION ---

def detect_switches(lines: List[Dict[str, Any]], normalizations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    switches = []
    for l in lines:
        txt = l["text"].strip()
        if re.search(r"Virtual\s*switches|DVUplink", txt, re.I):
            continue

        m_sw = re.search(r"\b(?:Standard|Distributed|Dist[a-z]*|Stand[a-z]*\s+)?Sw[i1l!t]+ch(?:\s*[:;\.]|\s+)\s*(.+)", txt, re.I)
        if not m_sw:
            m_sw = re.search(r"Sw[i1l!t]+ch\s*[:;]\s*(.+)", txt, re.I)

        if m_sw:
            raw_target = m_sw.group(1).strip()
            clean_target = re.split(r"(?:\s*\|\s*|\s+(?:ADD|EDIT|MANAGE|ACTIONS)\b|\s*\.{2,})", raw_target, maxsplit=1, flags=re.I)[0].strip()
            clean_target = re.sub(r"^[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*\s]+", "", clean_target).strip()
            clean_target = re.sub(r"[\s\.\…\×\<\>\(\)\[\]\{\}\|\-\:\;]+$", "", clean_target).strip()
            sw_name = apply_token_normalizations(clean_target, normalizations)

            if len(sw_name) >= 2:
                kw_x0 = l["x"]
                for tok in l["tokens"]:
                    ttxt = str(tok.get("text", "")).strip()
                    if re.search(r"Sw[i1l!t]+ch", ttxt, re.I):
                        kw_x0 = max(0.0, get_bounding_box(tok)[0] - 20.0)
                        break

                if not any(abs(s["y"] - l["y"]) <= 18.0 and s["name"] == sw_name for s in switches):
                    switches.append({
                        "name": sw_name,
                        "y": l["y"],
                        "x": kw_x0,
                        "collapsed": bool(re.search(r"^[>▶\+]", txt))
                    })

    switches.sort(key=lambda s: s["y"])
    for idx, sw in enumerate(switches):
        sw["y_start"] = sw["y"]
        sw["y_end"] = switches[idx + 1]["y"] if (idx + 1) < len(switches) else float("inf")
    return switches


# --- STAGE 3: PHYSICAL UPLINK EXTRACTION ---

def extract_physical_uplinks(tokens: List[Dict[str, Any]], sw_name: str, normalizations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not tokens:
        return [{
            "switch": sw_name, "interface": "No Uplink", "type": "Uplink",
            "ip": "", "purpose": "", "role": "None", "speed": ""
        }]

    phys_lines = cluster_lines(tokens, y_thresh=8.0, max_x_gap=80.0)
    found_uplinks = []
    seen_nics = set()

    for pline in phys_lines:
        ptxt = pline["text"].strip()
        if re.search(r"\(0\s*NIC|No\s*physical", ptxt, re.I):
            continue

        txt_clean = re.sub(r"\b(?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?\b", " ", ptxt, flags=re.I)

        for m in re.finditer(r"([xX!\*\+]\s*)?\b(?:v\s*[mn][a-z0-9]{1,4})\s*(\d+|[lI|])\b", txt_clean, re.I):
            status = m.group(1) or ""
            raw_digit = m.group(2)
            digit = "1" if raw_digit in ("l", "I", "|") else raw_digit
            nic_name = apply_token_normalizations(f"vmnic{digit}", normalizations)

            if nic_name not in seen_nics:
                seen_nics.add(nic_name)
                is_standby = bool(re.search(r"[xX!]", status)) or bool(re.search(r"^[xX!]\b|\bStandby\b", ptxt, re.I))
                if not is_standby:
                    for tok in pline["tokens"]:
                        if re.match(r"^[xX!]$", str(tok.get("text", "")).strip()):
                            is_standby = True
                            break

                sm = re.search(r"\b((?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?|\d+\s*(?:Gbit/s|Mbit/s))\b", ptxt, re.I)
                found_uplinks.append({
                    "switch": sw_name,
                    "interface": nic_name,
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "Standby Uplink" if is_standby else "Active Uplink",
                    "speed": sm.group(1).strip() if sm else ""
                })

    return found_uplinks if found_uplinks else [{
        "switch": sw_name, "interface": "No Uplink", "type": "Uplink",
        "ip": "", "purpose": "", "role": "None", "speed": ""
    }]


# --- STAGE 4: LOGICAL CARDS & VMKERNEL ANCHORING ---

def extract_logical_cards(tokens: List[Dict[str, Any]], sw_name: str, normalizations: List[Dict[str, str]], y_start: float) -> List[Dict[str, Any]]:
    if not tokens:
        return []

    lines = cluster_lines(tokens, y_thresh=10.0, max_x_gap=65.0)

    vmk_entries = []
    for l in lines:
        txt = re.sub(r"[^\x20-\x7E]+", " ", l["text"]).strip()
        m_vmk = re.search(r"\b(vm[kldo0-9a-zA-Z]{1,4})\s*[:;\s]\s*([0-9\.\s]{6,})", txt, re.I)
        if m_vmk:
            healed = heal_ipv4(m_vmk.group(2))
            if healed:
                raw_v = m_vmk.group(1).lower()
                m_dig = re.search(r"(\d+)", raw_v)
                if m_dig:
                    clean_v = f"vmk{m_dig.group(1)}"
                elif re.search(r"[oO0]$", raw_v):
                    clean_v = "vmk0"
                elif re.search(r"[dli1]$", raw_v):
                    clean_v = "vmk1"
                else:
                    clean_v = "vmk0"

                vmk_entries.append({"vmk": clean_v, "ip": healed, "y": l["y"]})

    vlan_rows = [
        l for l in lines
        if re.search(r"(?:V[L1I\|]?A?N\s*(?:[I1l\|]?D)?\s*[:;\s\-]|(?<!\w)ID\s*[:;]\s*(?:\d+|\-+))", l["text"], re.I)
    ]

    cards = []
    for v_idx, v_row in enumerate(vlan_rows):
        vy = v_row["y"]
        prev_bound = vlan_rows[v_idx - 1]["y"] if v_idx > 0 else y_start

        candidates = []
        for l in lines:
            ly = l["y"]
            if not (prev_bound < ly < vy and 4.0 <= (vy - ly) <= 58.0):
                continue
            txt = re.sub(r"[^\x20-\x7E]+", " ", l["text"]).strip()
            if re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\b\s*[:;]|ADD\s*NETWORKING|MANAGE\s*PHYS", txt, re.I):
                continue
            if re.search(r"(?:vmk[eao]rn|virt\s*ual\s*mach|machines?\s*\(|\bvmk\s*[a-zA-Z0-9]+\s*[:;])", txt, re.I):
                continue

            cleaned = re.sub(r"^[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*\s]+", "", txt).strip()
            cleaned = re.sub(r"(?:\s*\.{2,}|\s*\…|[\s\.\…\×\<\>\(\)\[\]\{\}\|\-\:\;])+$", "", cleaned).strip()
            cleaned = apply_token_normalizations(cleaned, normalizations)

            if len(cleaned) >= 2:
                candidates.append((vy - ly, cleaned, ly))

        if candidates:
            candidates.sort(key=lambda c: c[0])
            cards.append({"title": candidates[0][1], "y": candidates[0][2]})

    records = []
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

        clean_tokens = []
        for t in raw_tokens:
            tok_copy = dict(t)
            tok_copy["text"] = apply_token_normalizations(str(t.get("text", "")).strip(), normalizations)
            clean_tokens.append(tok_copy)

        header_lines = cluster_lines(clean_tokens, y_thresh=14.0, max_x_gap=None)
        switches = detect_switches(header_lines, normalizations)
        if not switches:
            return []

        sidebar_x = max(0.0, min(s["x"] for s in switches) - 15.0)
        records: List[Dict[str, Any]] = []

        for sw in switches:
            sw_name = sw["name"]
            if sw.get("collapsed", False) and (sw["y_end"] - sw["y_start"]) < 45.0:
                continue

            band_tokens = [
                t for t in clean_tokens
                if get_bounding_box(t)[0] >= sidebar_x
                and (sw["y_start"] + 14.0 <= get_bounding_box(t)[1] < sw["y_end"] - 5.0)
                and t["text"] not in (">", "▶", "v", "▼", "...", "|")
            ]

            if not band_tokens:
                records.append({
                    "switch": sw_name, "interface": "No Uplink", "type": "Uplink",
                    "ip": "", "purpose": "", "role": "None", "speed": ""
                })
                continue

            phys_anchor_x = None
            for t in band_tokens:
                ttxt = str(t.get("text", "")).strip()
                if re.search(r"(?:Physical\s*Adapters|DVUplink|NIC\s*Adapters|\bvmnic|\bvmnlc|\bvmkc)", ttxt, re.I):
                    tx0 = get_bounding_box(t)[0]
                    if phys_anchor_x is None or tx0 < phys_anchor_x:
                        phys_anchor_x = tx0

            if phys_anchor_x and phys_anchor_x > (sidebar_x + 150.0):
                logical_tokens = [t for t in band_tokens if get_bounding_box(t)[0] < (phys_anchor_x - 10.0)]
                physical_tokens = [t for t in band_tokens if get_bounding_box(t)[0] >= (phys_anchor_x - 15.0)]
            else:
                logical_tokens = band_tokens
                physical_tokens = []

            records.extend(extract_physical_uplinks(physical_tokens, sw_name, normalizations))
            records.extend(extract_logical_cards(logical_tokens, sw_name, normalizations, sw["y_start"]))

        switches_with_data = {r["switch"] for r in records if r["interface"] != "No Uplink" or r["purpose"]}
        cleaned_records = [
            r for r in records
            if not (r["switch"] in switches_with_data and r["interface"] == "No Uplink" and not r["purpose"])
        ]

        return cleaned_records
