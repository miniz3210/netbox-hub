"""
VMware ESXi & vDS High-Recall Universal Topology Extractor (v4.8.23)
Design Philosophy: Maximize Recall (Zero Missing Switches, Port Groups, or Uplinks).
Key Guarantees:
1. Strict Left-Sidebar Isolation: Anchors switch X-coordinates directly on the Switch
   header keyword so left-hand navigation menus are 100% excluded.
2. Safe Right-Column Split: Separates Physical Adapters only when a true right-hand
   adapter box exists to the right of the logical cards; never blindly slices at 48%.
3. Inclusive Port Group Extraction: Strips non-ASCII icon/ellipsis artifacts instead
   of dropping lines, and captures all card titles regardless of 'VLAN ID: --' OCR noise.
4. Multi-Match Uplink Extraction: Uses re.finditer and OCR character recovery (l/I/| -> 1)
   so closely spaced adapters like vmnic1 are never skipped.
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
    max_x_gap: Optional[float] = 65.0
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
        max_vmk_dist = float(geom_cfg.get("vmk_to_title_max_dist", 160.0))

        # Anchor noise filters to full-string matches so partial words never delete Port Groups
        if noise_filters:
            escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
            noise_re = re.compile(r"^(?:" + r"|".join(escaped) + r")$", re.I)
        else:
            noise_re = None

        # -------------------------------------------------------------
        # Step 1: Detect Switch Headers & Isolate Left Navigation Sidebar
        # -------------------------------------------------------------
        # Use max_x_gap=45.0 so Left Sidebar tokens never merge with Switch Headers
        header_segments = cluster_tokens_into_lines(raw_tokens, y_thresh=14.0, max_x_gap=45.0)
        switch_headers = []

        for seg in header_segments:
            line_text = seg["text"]

            # Ignore DVUplinks container titles and page-level 'Virtual switches' heading
            if re.search(r"DVUplink|Virtual\s*switches", line_text, re.I):
                continue

            # Match any variation of Standard/Distributed Switch or Switch: <name>
            has_prefix = bool(re.search(r"(?:Standard|Distributed|Dist[a-z]*|Stand[a-z]*)\s*Sw[a-z]*", line_text, re.I))
            has_colon = bool(re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\s*[:;]", line_text, re.I))

            if not (has_prefix or has_colon):
                continue

            parts = re.split(r"(?:Sw[i1l!t]+ch|Bridge)\s*[:;]?\s*", line_text, maxsplit=1, flags=re.I)
            if len(parts) < 2:
                continue

            after_switch = parts[1].strip()
            sw_candidate = re.split(r"(?:\s+\||\s+ADD\b|\s+EDIT\b|\s+MANAGE\b|\s+ACTIONS\b|\||\.{2,})", after_switch, maxsplit=1, flags=re.I)[0].strip()

            id_match = re.match(r"^([A-Za-z0-9_\-\.]+)", sw_candidate)
            if not id_match:
                continue

            raw_sw_name = id_match.group(1).strip()
            if len(raw_sw_name) < 2:
                continue

            sw_name = apply_normalizations(raw_sw_name, normalizations)

            # Find exact X0 of the Switch header keyword (never walk across into the sidebar)
            kw_x0 = seg["x"]
            for tok in seg["tokens"]:
                ttxt = str(tok.get("text", "")).strip()
                if re.search(r"(?:Standard|Distributed|Dist[a-z]*|Stand[a-z]*|Sw[i1l!t]+ch|Bridge)", ttxt, re.I):
                    kw_x0 = max(0.0, get_bounding_box(tok)[0] - 22.0)
                    break

            # Deduplicate headers detected on the same Y band
            if any(abs(existing["y_top"] - seg["y"]) <= 18.0 and existing["name"] == sw_name for existing in switch_headers):
                continue

            switch_headers.append({
                "name": sw_name,
                "y_top": seg["y"],
                "x_left": kw_x0
            })

        switch_headers.sort(key=lambda s: s["y_top"])
        if not switch_headers:
            return []

        # All switch headers in the main canvas align at min_switch_x (~165px with sidebar, ~15px without)
        min_switch_x = min(sh["x_left"] for sh in switch_headers)
        sidebar_cutoff_x = max(0.0, min_switch_x - 10.0)

        for idx, sh in enumerate(switch_headers):
            sh["y_start"] = sh["y_top"]
            if idx + 1 < len(switch_headers):
                sh["y_end"] = switch_headers[idx + 1]["y_top"]
            else:
                sh["y_end"] = float("inf")

        # -------------------------------------------------------------
        # Step 2: Extract Uplinks & Port Groups per Switch Band
        # -------------------------------------------------------------
        extracted_records: List[Dict[str, Any]] = []

        for sh in switch_headers:
            sw_name = sh["name"]

            inner_tokens = []
            for t in raw_tokens:
                bx0, by0, bx1, by1 = get_bounding_box(t)
                text = str(t.get("text", "")).strip()

                # Strictly cut off the entire Left Navigation Sidebar
                if bx0 < sidebar_cutoff_x:
                    continue
                if not (sh["y_start"] + 14.0 <= by0 < sh["y_end"] - 6.0):
                    continue
                if text in (">", "▶", "v", "▼", "...", "|"):
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

            # Determine where Logical Cards sit vs where the Right-Hand Physical Box sits
            logical_card_x_positions = [
                get_bounding_box(t)[0] for t in inner_tokens
                if re.search(r"(?:V[L1I]AN|Virt\s*ual\s*Mach|VMk[a-z]*\s*Port|vmk\s*[oO0-9])", str(t.get("text", "")), re.I)
            ]
            min_phys_x_threshold = (max(logical_card_x_positions) + 90.0) if logical_card_x_positions else (sidebar_cutoff_x + 260.0)

            phys_anchor_x = None
            for t in inner_tokens:
                txt = str(t.get("text", "")).strip()
                x0, _, _, _ = get_bounding_box(t)
                if x0 < min_phys_x_threshold:
                    continue
                if re.search(r"(?:Physical\s*Adapters|DVUplink|NIC\s*Adapters|(?:v\s*m|v\s*n+)[a-z0-9]*[0-9lI|]|No\s*physical\s*network)", txt, re.I):
                    if phys_anchor_x is None or x0 < phys_anchor_x:
                        phys_anchor_x = x0

            if phys_anchor_x is not None:
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < (phys_anchor_x - 12.0)]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= (phys_anchor_x - 15.0)]
            else:
                # If no right-hand Physical Adapter box exists, 100% of tokens are Logical Cards
                logical_tokens = inner_tokens
                physical_tokens = []

            # ---------------------------------------------------------
            # A. High-Recall Physical Uplink Extraction
            # ---------------------------------------------------------
            physical_records = []
            vmnic_matches = []

            # Cluster physical tokens by line so speed and standby icons stay paired
            phys_lines = cluster_tokens_into_lines(physical_tokens, y_thresh=8.0, max_x_gap=80.0)

            for pline in phys_lines:
                txt = pline["text"].strip()
                if re.search(r"\(0\s*NIC\s*Adapters\)|No\s*physical\s*network", txt, re.I):
                    continue
                if re.search(r"DVUplink", txt, re.I) and not re.search(r"\bvm[nN]", txt, re.I):
                    continue

                # Separate glued speed numbers (e.g. 'vmnic110000' -> 'vmnic1 10000')
                txt_spaced = re.sub(r"([0-9lI|])(100000|40000|25000|10000|1000|100)\b", r"\1 \2", txt)
                # Strip speed phrases before extracting adapter index
                txt_clean = re.sub(r"\b(?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?\b", " ", txt_spaced, flags=re.I)

                # Find all NIC patterns (handles vmnic0, vmnicl, vmnicI, vmnkc3, vnnic1, eno1, eth0)
                for m in re.finditer(r"([xX!\*\+]\s*)?(?:v\s*m\s*n\s*[i1lLkK]?\s*[cC]?|v\s*n+\s*[i1lLkK]?\s*[cC])\s*([0-9lI|])\b", txt_clean, re.I):
                    status_prefix = m.group(1) or ""
                    raw_digit = m.group(2)
                    digit = "1" if raw_digit in ("l", "I", "|") else raw_digit
                    clean_nic = apply_normalizations(f"vmnic{digit}", normalizations)

                    is_standby = bool(re.search(r"[xX!]", status_prefix)) or bool(re.search(r"^[xX!]\b|\bStandby\b", txt, re.I))
                    if not is_standby:
                        for tok in pline["tokens"]:
                            if re.match(r"^[xX!]$", str(tok.get("text", "")).strip()):
                                is_standby = True
                                break

                    sm = re.search(r"\b((?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?|\d+\s*(?:Gbit/s|Mbit/s))\b", txt_spaced, re.I)
                    speed_str = sm.group(1).strip() if sm else ""

                    vmnic_matches.append({
                        "nic": clean_nic,
                        "standby": is_standby,
                        "speed": speed_str,
                        "y": pline["y"]
                    })

            seen_nics = set()
            for vm in sorted(vmnic_matches, key=lambda x: (x["y"], x["nic"])):
                if vm["nic"] in seen_nics:
                    continue
                seen_nics.add(vm["nic"])

                physical_records.append({
                    "switch": sw_name,
                    "interface": vm["nic"],
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "Standby Uplink" if vm["standby"] else "Active Uplink",
                    "speed": vm["speed"]
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
            # B. High-Recall Inclusive Logical Column Extraction
            # ---------------------------------------------------------
            logical_lines = cluster_tokens_into_lines(logical_tokens, y_thresh=y_thresh, max_x_gap=65.0)

            vmk_entries = []
            for line_item in logical_lines:
                txt = line_item["text"].strip()
                m_vmk = re.search(r"\b(vmk\s*[oO0-9lI]+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt, re.I)
                if m_vmk:
                    raw_v = re.sub(r"\s+", "", m_vmk.group(1)).lower()
                    raw_v = re.sub(r"vmk[o]", "vmk0", raw_v)
                    raw_v = re.sub(r"vmk[li]", "vmk1", raw_v)
                    vmk_entries.append({
                        "vmk": raw_v,
                        "ip": m_vmk.group(2).strip(),
                        "y": line_item["y"]
                    })

            service_titles = []
            for line_item in logical_lines:
                raw_txt = line_item["text"].strip()

                # Replace non-ASCII icon/ellipsis glyphs with space instead of dropping the line!
                txt = re.sub(r"[^\x20-\x7E]+", " ", raw_txt).strip()
                if not txt:
                    continue

                # Skip Switch headers or action bar items
                if re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\b\s*[:;]", txt, re.I):
                    continue
                if re.search(r"\b(?:ADD\s*NETWORKING|MANAGE\s*PHYS|ACTIONS|REFRESH)\b", txt, re.I):
                    continue

                # Skip Card sub-metadata lines (VLAN ID, VMkernel Ports count, Virtual Machines count, IP lines)
                if re.search(r"\bV[L1I]AN\s*ID\b|\bVLANID\b|\bID\s*[:;]\s*(?:\d+|\-+)", txt, re.I):
                    continue
                if re.search(r"(?:vmk[eao]rn[eao]l|vmk[eao]rm[eao]l)\s*ports?|\bPorts?\s*\(\s*\d+\s*\)", txt, re.I):
                    continue
                if re.search(r"virt\s*ual\s*mach|machines?\s*\(\s*\d+\s*\)", txt, re.I):
                    continue
                if re.search(r"\bvmk\s*[oO0-9lI]+\s*[:\s]\s*\d{1,3}\.", txt, re.I):
                    continue
                if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", txt):
                    continue
                if re.search(r"No\s*associated\s*port\s*groups", txt, re.I):
                    continue
                if re.search(r"(?i)sauqsey", txt):
                    continue

                # Strip leading/trailing punctuation and standalone icon letters (e.g. 'v ' or '> ')
                cleaned_title = re.sub(r"^(?:[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*]+|[vV]\s+)\s*", "", txt).strip()
                cleaned_title = re.sub(r"[\s\.\×\<\>\(\)\[\]\{\}\|\-\:\;]+$", "", cleaned_title).strip()
                cleaned_title = apply_normalizations(cleaned_title, normalizations)

                if len(cleaned_title) <= 2:
                    continue
                if noise_re and noise_re.match(cleaned_title):
                    continue

                service_titles.append({
                    "title": cleaned_title,
                    "y": line_item["y"]
                })

            service_titles.sort(key=lambda s: s["y"])

            assigned_titles = set()
            for v in vmk_entries:
                candidates = [st for st in service_titles if 0 <= (v["y"] - st["y"]) <= max_vmk_dist]
                if candidates:
                    candidates.sort(key=lambda c: v["y"] - c["y"])
                    purpose_name = candidates[0]["title"]
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

            for st_item in service_titles:
                title = st_item["title"]
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
