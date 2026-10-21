"""
VMware ESXi & vDS Universal High-Recall Extractor (v4.8.27)
Standardized, Deterministic, Zero-Dropping Entity Pipeline.
"""

import re
from typing import List, Dict, Any, Optional
from .base import BaseTopologyExtractor, get_bounding_box


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
    m = re.search(r"(\d[\d\.]{6,16}\d)", clean)
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


def cluster_lines(tokens: List[Dict[str, Any]], y_thresh: float = 12.0) -> List[Dict[str, Any]]:
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

    clustered = []
    for r in rows:
        r.sort(key=lambda x: get_bounding_box(x)[0])
        txt = " ".join(str(x.get("text", "")).strip() for x in r if str(x.get("text", "")).strip())
        min_x = min(get_bounding_box(x)[0] for x in r)
        min_y = min(get_bounding_box(x)[1] for x in r)
        clustered.append({
            "text": txt,
            "tokens": r,
            "x": min_x,
            "y": min_y
        })
    clustered.sort(key=lambda l: l["y"])
    return clustered


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

        # Stage 1: Pre-normalization
        clean_tokens = []
        for t in raw_tokens:
            tok_copy = dict(t)
            tok_copy["text"] = apply_token_normalizations(str(t.get("text", "")).strip(), normalizations)
            clean_tokens.append(tok_copy)

        # Stage 2: Switch Header Extraction
        lines = cluster_lines(clean_tokens, y_thresh=14.0)
        switches = []
        for l in lines:
            txt = l["text"].strip()
            if re.search(r"Virtual\s*switches|DVUplink", txt, re.I):
                continue
            if re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\b", txt, re.I):
                parts = re.split(r"\b(?:Sw[i1l!t]+ch|Bridge)\b\s*[:;]?\s*", txt, maxsplit=1, flags=re.I)
                if len(parts) > 1 and parts[1].strip():
                    raw_name = parts[1].strip()
                    clean_name = re.split(r"(?:\s*\|\s*|\s+(?:ADD|EDIT|MANAGE|ACTIONS)\b|\s*\.{2,})", raw_name, maxsplit=1, flags=re.I)[0].strip()
                    clean_name = re.sub(r"^[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*\s]+", "", clean_name).strip()
                    clean_name = re.sub(r"[\s\.\…\×\<\>\(\)\[\]\{\}\|\-\:\;]+$", "", clean_name).strip()
                    clean_name = apply_token_normalizations(clean_name, normalizations)

                    if len(clean_name) >= 2:
                        kw_x0 = l["x"]
                        for tok in l["tokens"]:
                            ttxt = str(tok.get("text", "")).strip()
                            if re.search(r"(?:Standard|Distributed|Dist[a-z]*|Sw[i1l!t]+ch|Bridge)", ttxt, re.I):
                                kw_x0 = max(0.0, get_bounding_box(tok)[0] - 20.0)
                                break

                        if not any(abs(s["y"] - l["y"]) <= 18.0 and s["name"] == clean_name for s in switches):
                            switches.append({
                                "name": clean_name,
                                "y": l["y"],
                                "x": kw_x0,
                                "collapsed": bool(re.match(r"^[>▶\+]", txt))
                            })

        if not switches:
            return []

        switches.sort(key=lambda s: s["y"])
        for idx, sw in enumerate(switches):
            sw["y_start"] = sw["y"]
            sw["y_end"] = switches[idx + 1]["y"] if (idx + 1) < len(switches) else float("inf")

        sidebar_x = max(0.0, min(s["x"] for s in switches) - 15.0)
        records: List[Dict[str, Any]] = []

        # Stage 3: Extract Records per Switch Band
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

            # Determine Physical Adapter Column Anchor
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

            # Physical Uplink Extraction
            found_uplinks = []
            phys_lines = cluster_lines(physical_tokens, y_thresh=8.0)
            seen_nics = set()

            for pline in phys_lines:
                ptxt = pline["text"].strip()
                if re.search(r"\(0\s*NIC|No\s*physical", ptxt, re.I):
                    continue

                for m in re.finditer(r"([xX!\*\+]\s*)?\b(?:v\s*[mn][a-z0-9]{1,4})\s*([0-9lI|])\b", ptxt, re.I):
                    status = m.group(1) or ""
                    digit = "1" if m.group(2) in ("l", "I", "|") else m.group(2)
                    nic_name = f"vmnic{digit}"

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

            if not found_uplinks:
                records.append({
                    "switch": sw_name, "interface": "No Uplink", "type": "Uplink",
                    "ip": "", "purpose": "", "role": "None", "speed": ""
                })
            else:
                records.extend(found_uplinks)

            # Logical Extraction: VMkernel & Inverted Port Group Filtering
            log_lines = cluster_lines(logical_tokens, y_thresh=10.0)
            vmk_items = []
            pg_titles = []

            for ll in log_lines:
                ltxt = re.sub(r"[^\x20-\x7E]+", " ", ll["text"]).strip()
                if not ltxt:
                    continue

                m_vmk = re.search(r"\b(vmk\s*[a-zA-Z0-9]{1,2})\s*[:;\s]\s*([0-9\.\s]{7,})", ltxt, re.I)
                if m_vmk:
                    healed_ip = heal_ipv4(m_vmk.group(2))
                    if healed_ip:
                        raw_v = re.sub(r"\s+", "", m_vmk.group(1)).lower()
                        suf = raw_v[3:]
                        clean_v = "vmk0" if suf in ("o", "0") else f"vmk{suf}" if suf.isdigit() else "vmk1"
                        vmk_items.append({"vmk": clean_v, "ip": healed_ip, "y": ll["y"]})
                        continue

                # Exclude metadata rows
                if re.search(r"\bV[L1I\|]?A?N\s*[I1l\|]?D|\bID\s*[:;]|\bVirtual\s*Machines|\bVMkernel\s*Ports|No\s*associated|\bSwitch\b", ltxt, re.I):
                    continue

                clean_title = re.sub(r"^[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*\s]+", "", ltxt).strip()
                clean_title = re.sub(r"(?:\s*\.{2,}|\s*\…|[\s\.\…\×\<\>\(\)\[\]\{\}\|\-\:\;])+$", "", clean_title).strip()
                clean_title = apply_token_normalizations(clean_title, normalizations)

                if len(clean_title) >= 2:
                    pg_titles.append({"title": clean_title, "y": ll["y"]})

            assigned_pgs = set()
            for v in vmk_items:
                candidates = [p for p in pg_titles if p["y"] <= v["y"]]
                purpose = candidates[-1]["title"] if candidates else ("Management Network" if v["vmk"] == "vmk0" else v["vmk"])
                if candidates:
                    assigned_pgs.add(purpose)
                records.append({
                    "switch": sw_name, "interface": v["vmk"], "type": "VMkernel",
                    "ip": v["ip"], "purpose": purpose, "role": "None", "speed": ""
                })

            for p in pg_titles:
                if p["title"] not in assigned_pgs:
                    assigned_pgs.add(p["title"])
                    records.append({
                        "switch": sw_name, "interface": "", "type": "Port Group",
                        "ip": "", "purpose": p["title"], "role": "None", "speed": ""
                    })

        return records
