"""
VMware ESXi & vDS Structural Anchor Topology Extractor (v4.8.22)
Architecture:
1. X-Gap Aware Line Clustering: Prevents left-column and right-column tokens from merging.
2. Token-Split Switch Header Detection: Extracts the identifier immediately following 'Switch:' 
   without brittle full-line regexes or word-boundary traps.
3. Positive VLAN-ID Card Anchoring: Identifies Port Groups by locating 'VLAN ID' anchors 
   and grabbing the card title directly above them, eliminating noise-filter whack-a-mole.
"""

import re
from typing import List, Dict, Any, Optional
from .base import BaseTopologyExtractor, get_bounding_box


def apply_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    for rule in normalizations:
        pat = rule.get("pattern")
        rep = rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


def cluster_tokens_into_lines(
    tokens: List[Dict[str, Any]], 
    y_thresh: float = 10.0, 
    max_x_gap: Optional[float] = None
) -> List[Dict[str, Any]]:
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

    final_lines: List[List[Dict[str, Any]]] = []
    for row in raw_rows:
        row.sort(key=lambda x: get_bounding_box(x)[0])
        if max_x_gap is None:
            final_lines.append(row)
        else:
            curr_segment = [row[0]]
            for tok in row[1:]:
                prev_x1 = get_bounding_box(curr_segment[-1])[2]
                curr_x0 = get_bounding_box(tok)[0]
                if (curr_x0 - prev_x1) > max_x_gap:
                    final_lines.append(curr_segment)
                    curr_segment = [tok]
                else:
                    curr_segment.append(tok)
            final_lines.append(curr_segment)

    clustered = []
    for line in final_lines:
        combined_text = " ".join(str(x.get("text", "")).strip() for x in line if str(x.get("text", "")).strip())
        min_x = min(get_bounding_box(x)[0] for x in line)
        min_y = min(get_bounding_box(x)[1] for x in line)
        max_x = max(get_bounding_box(x)[2] for x in line)
        max_y = max(get_bounding_box(x)[3] for x in line)
        clustered.append({
            "text": combined_text,
            "tokens": line,
            "box": [min_x, min_y, max_x - min_x, max_y - min_y],
            "y": min_y,
            "x": min_x
        })

    clustered.sort(key=lambda x: (x["y"], x["x"]))
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
        noise_filters = cfg.get("noise_filters", [])
        geom_cfg = cfg.get("geometry", {})
        y_thresh = float(geom_cfg.get("line_cluster_y_thresh", 10.0))

        if noise_filters:
            escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
            noise_re = re.compile(r"(?:" + r"|".join(escaped) + r")", re.I)
        else:
            noise_re = None

        # -------------------------------------------------------------
        # Step 1: Structural Switch Header Detection (Two-Stage Split)
        # -------------------------------------------------------------
        header_rows = cluster_tokens_into_lines(raw_tokens, y_thresh=16.0, max_x_gap=None)
        switch_headers = []

        for row_item in header_rows:
            line_text = row_item["text"]

            # Ignore DVUplink container titles or Port Group lines
            if re.search(r"DVUplink", line_text, re.I):
                continue

            # Check if row contains 'Switch' (or 'Bridge') followed by ':' or accompanied by action buttons
            has_switch_kw = bool(re.search(r"(?:Switch|Swtich|Swltch|Bridge)\s*[:;]", line_text, re.I))
            has_action_bar = bool(re.search(r"(?:MANAGE\s*PHYS|ADD\s*NETWORK)", line_text, re.I)) and bool(re.search(r"(?:Switch|Swtich|Swltch|Bridge)", line_text, re.I))

            if not (has_switch_kw or has_action_bar):
                continue

            # Split right after 'Switch:' (immune to typos in 'Distributed'/'Standard' and missing spaces)
            parts = re.split(r"(?:Switch|Swtich|Swltch|Bridge)\s*[:;]?\s*", line_text, maxsplit=1, flags=re.I)
            if len(parts) < 2:
                continue

            after_switch = parts[1].strip()
            # Cut off at any action button, pipe separator '|', or ellipsis
            sw_candidate = re.split(r"(?:\s+\||\s+ADD\b|\s+EDIT\b|\s+MANAGE\b|\s+ACTIONS\b|\||\.{2,})", after_switch, maxsplit=1, flags=re.I)[0].strip()

            # Extract clean switch identifier
            id_match = re.match(r"^([A-Za-z0-9_\-\.]+)", sw_candidate)
            if not id_match:
                continue

            raw_sw_name = id_match.group(1).strip()
            if len(raw_sw_name) < 2:
                continue

            sw_name = apply_normalizations(raw_sw_name, normalizations)

            tokens_in_line = row_item["tokens"]
            header_x0 = get_bounding_box(tokens_in_line[0])[0]

            switch_headers.append({
                "name": sw_name,
                "y_top": row_item["y"],
                "x_left": header_x0
            })

        switch_headers.sort(key=lambda s: s["y_top"])
        if not switch_headers:
            return []

        min_switch_x = min(sh["x_left"] for sh in switch_headers)
        sidebar_cutoff_x = max(0.0, min_switch_x - 15.0)

        for idx, sh in enumerate(switch_headers):
            sh["y_start"] = sh["y_top"]
            if idx + 1 < len(switch_headers):
                sh["y_end"] = switch_headers[idx + 1]["y_top"]
            else:
                sh["y_end"] = float("inf")

        # -------------------------------------------------------------
        # Step 2: Positive Structural Extraction per Switch Band
        # -------------------------------------------------------------
        extracted_records: List[Dict[str, Any]] = []

        for sh in switch_headers:
            sw_name = sh["name"]

            inner_tokens = []
            for t in raw_tokens:
                bx0, by0, bx1, by1 = get_bounding_box(t)
                text = str(t.get("text", "")).strip()

                if bx0 < sidebar_cutoff_x:
                    continue
                if not (sh["y_start"] + 14.0 <= by0 < sh["y_end"] - 5.0):
                    continue
                if text.startswith(">") or text.startswith("▶"):
                    continue

                inner_tokens.append(t)

            if not inner_tokens:
                extracted_records.append({
                    "switch": sw_name,
                    "interface": "No Uplink",
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "None",
                    "speed": ""
                })
                continue

            # Column Split: Find Physical Adapter Box Anchor on Right Side
            phys_anchor_x = None
            for t in inner_tokens:
                txt = str(t.get("text", "")).strip()
                if re.search(r"(?:Physical\s*Adapters|DVUplink|vm[nN][kKiI1lL]c\s*\d+|No\s*physical\s*network\s*adapters)", txt, re.I):
                    x0, _, _, _ = get_bounding_box(t)
                    if x0 > (sidebar_cutoff_x + 120.0):
                        if phys_anchor_x is None or x0 < phys_anchor_x:
                            phys_anchor_x = x0

            if phys_anchor_x is not None:
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < (phys_anchor_x - 10.0)]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= (phys_anchor_x - 15.0)]
            else:
                x_min_band = min(get_bounding_box(t)[0] for t in inner_tokens)
                x_max_band = max(get_bounding_box(t)[2] for t in inner_tokens)
                x_mid_band = x_min_band + (x_max_band - x_min_band) * 0.5
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < x_mid_band]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= x_mid_band]

            # ---------------------------------------------------------
            # A. Physical Uplinks Extraction
            # ---------------------------------------------------------
            physical_records = []
            vmnic_matches = []

            for pt in physical_tokens:
                txt = str(pt.get("text", "")).strip()
                if re.search(r"\(0\s*NIC\s*Adapters\)", txt, re.I):
                    continue

                m = re.search(r"([xX!\*\-\+]\s*)?.*?(vm[nN][kKiI1lL]c\s*\d+|eno\s*\d+|ens\s*\d+|eth\s*\d+|bond\s*\d+)", txt, re.I)
                if m:
                    status_prefix = m.group(1) or ""
                    raw_nic = re.sub(r"\s+", "", m.group(2)).lower()
                    clean_nic = re.sub(r"^vm[nN][kKiI1lL]c", "vmnic", raw_nic)
                    clean_nic = apply_normalizations(clean_nic, normalizations)

                    is_standby = bool(re.search(r"[xX!]", status_prefix)) or bool(re.search(r"\bStandby\b", txt, re.I))

                    if not is_standby:
                        pt_y = get_bounding_box(pt)[1]
                        pt_x = get_bounding_box(pt)[0]
                        for other_pt in physical_tokens:
                            otxt = str(other_pt.get("text", "")).strip()
                            if re.match(r"^[xX!]$", otxt):
                                if abs(get_bounding_box(other_pt)[1] - pt_y) <= 12.0:
                                    if 0.0 <= (pt_x - get_bounding_box(other_pt)[2]) <= 40.0:
                                        is_standby = True
                                        break

                    vmnic_matches.append({
                        "nic": clean_nic,
                        "standby": is_standby,
                        "y": get_bounding_box(pt)[1]
                    })

            seen_nics = set()
            for vm in sorted(vmnic_matches, key=lambda x: x["y"]):
                if vm["nic"] in seen_nics:
                    continue
                seen_nics.add(vm["nic"])

                nic_speed = ""
                for pt in physical_tokens:
                    if abs(get_bounding_box(pt)[1] - vm["y"]) < 20.0:
                        sm = re.search(r"(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000)", str(pt.get("text", "")), re.I)
                        if sm:
                            nic_speed = sm.group(1).strip()
                            break

                physical_records.append({
                    "switch": sw_name,
                    "interface": vm["nic"],
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "Standby Uplink" if vm["standby"] else "Active Uplink",
                    "speed": nic_speed
                })

            if not physical_records:
                physical_records.append({
                    "switch": sw_name,
                    "interface": "No Uplink",
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "None",
                    "speed": ""
                })
            extracted_records.extend(physical_records)

            # ---------------------------------------------------------
            # B. Positive Card-Anchor Logical Extraction (VLAN ID Anchors)
            # ---------------------------------------------------------
            # Segment logical tokens with max_x_gap=80 so separate columns never merge
            logical_lines = cluster_tokens_into_lines(logical_tokens, y_thresh=y_thresh, max_x_gap=80.0)

            # 1. Locate all VLAN ID anchors inside cards
            vlan_anchors = []
            for idx, l_item in enumerate(logical_lines):
                if re.search(r"\bVLAN\b", l_item["text"], re.I):
                    vlan_anchors.append(l_item)

            # 2. Extract VMkernel ports
            vmk_entries = []
            for l_item in logical_lines:
                txt = l_item["text"].strip()
                m_vmk = re.search(r"\b(vmk\s*[oO0-9]+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt, re.I)
                if m_vmk:
                    clean_vmk = re.sub(r"\s+", "", m_vmk.group(1))
                    clean_vmk = re.sub(r"(?i)vmk[oO]", "vmk0", clean_vmk).lower()
                    vmk_entries.append({
                        "vmk": clean_vmk,
                        "ip": m_vmk.group(2).strip(),
                        "y": l_item["y"]
                    })

            # 3. For each VLAN anchor, look strictly upwards (5px to 45px) for the Card Title
            card_titles = []
            for v_idx, v_anchor in enumerate(vlan_anchors):
                vy = v_anchor["y"]
                vx = v_anchor["x"]
                prev_vy = vlan_anchors[v_idx - 1]["y"] if v_idx > 0 else sh["y_start"]

                candidates = []
                for l_item in logical_lines:
                    ly = l_item["y"]
                    lx = l_item["x"]
                    txt = l_item["text"].strip()

                    if not (prev_vy < ly < vy and (vy - ly) <= 48.0):
                        continue
                    if abs(lx - vx) > 120.0:
                        continue
                    if re.search(r"\b(?:VLAN|Virtual\s*Machines|VMkernel)\b", txt, re.I):
                        continue

                    cleaned = re.sub(r"^[>\s\(\)\[\]\{\}\<\>▼▶✓✔\-\—\.\:\;]+", "", txt).strip()
                    cleaned = re.sub(r"[\s\.\…\×\<\>\(\)\[\]\{\}]+$", "", cleaned).strip()
                    cleaned = apply_normalizations(cleaned, normalizations)

                    if len(cleaned) > 2 and not (noise_re and noise_re.search(cleaned)):
                        candidates.append((vy - ly, cleaned, ly))

                if candidates:
                    # Closest valid line directly above VLAN ID is the Port Group Title
                    candidates.sort(key=lambda c: c[0])
                    _, best_title, title_y = candidates[0]
                    next_vy = vlan_anchors[v_idx + 1]["y"] if (v_idx + 1) < len(vlan_anchors) else float("inf")
                    card_titles.append({
                        "title": best_title,
                        "y": title_y,
                        "vlan_y": vy,
                        "next_vlan_y": next_vy
                    })

            # 4. Bind VMkernel Ports to their enclosing Card (between vlan_y and next_vlan_y)
            assigned_titles = set()
            for v in vmk_entries:
                matched_card = None
                for card in card_titles:
                    if card["y"] <= v["y"] < card["next_vlan_y"] and (v["y"] - card["vlan_y"]) <= 90.0:
                        matched_card = card
                        break

                if matched_card:
                    purpose_name = matched_card["title"]
                    assigned_titles.add(purpose_name)
                else:
                    purpose_name = "Management Network" if v["vmk"] == "vmk0" else v["vmk"]

                extracted_records.append({
                    "switch": sw_name,
                    "interface": v["vmk"],
                    "type": "VMkernel",
                    "ip": v["ip"],
                    "purpose": purpose_name,
                    "role": "None",
                    "speed": ""
                })

            for card in card_titles:
                title = card["title"]
                if title in assigned_titles:
                    continue
                assigned_titles.add(title)
                extracted_records.append({
                    "switch": sw_name,
                    "interface": "",
                    "type": "Port Group",
                    "ip": "",
                    "purpose": title,
                    "role": "None",
                    "speed": ""
                })

        return extracted_records
