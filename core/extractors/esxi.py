"""
VMware ESXi & vDS Pure Python Geometric Topology Extractor (v4.8.7)
Universal, Deterministic, 100% Configuration-Driven.
Clusters horizontal lines first to guarantee tokens split by OCR
(e.g. ['Distributed', 'Switch:', 'dvSwitch0']) are seamlessly united.
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


def cluster_tokens_into_lines(tokens: List[Dict[str, Any]], y_thresh: float = 8.0) -> List[Dict[str, Any]]:
    """
    Groups visual word tokens that lie on the same horizontal line (within y_thresh),
    sorting from left to right, retaining individual tokens and full concatenated text.
    """
    if not tokens:
        return []

    sorted_tokens = sorted(tokens, key=lambda t: (get_bounding_box(t)[1], get_bounding_box(t)[0]))
    lines: List[List[Dict[str, Any]]] = []

    for t in sorted_tokens:
        _, y0, _, y1 = get_bounding_box(t)
        mid_y = (y0 + y1) / 2.0

        placed = False
        for line in lines:
            line_mid_y = sum((get_bounding_box(x)[1] + get_bounding_box(x)[3]) / 2.0 for x in line) / len(line)
            if abs(mid_y - line_mid_y) <= y_thresh:
                line.append(t)
                placed = True
                break
        if not placed:
            lines.append([t])

    clustered = []
    for line in lines:
        line.sort(key=lambda x: get_bounding_box(x)[0])
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

    clustered.sort(key=lambda x: x["y"])
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
        if noise_filters:
            escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
            noise_re = re.compile(r"(?:" + r"|".join(escaped) + r")", re.I)
        else:
            noise_re = None

        # -------------------------------------------------------------
        # Step 1: Detect Switch Headers (Line-Clustering First!)
        # -------------------------------------------------------------
        all_lines = cluster_tokens_into_lines(raw_tokens, y_thresh=8.0)
        switch_headers = []

        for line_item in all_lines:
            tokens_in_line = line_item["tokens"]

            # Locate the token matching Switch or Bridge
            switch_tok_idx = None
            for idx, tok in enumerate(tokens_in_line):
                txt = str(tok.get("text", "")).strip()
                if re.search(r"(?:Standard|Distributed|Bridge|\bSwitch\b)", txt, re.I):
                    switch_tok_idx = idx
                    break

            if switch_tok_idx is None:
                continue

            # Identify start token (include preceding chevron/arrow if within 35px)
            start_idx = switch_tok_idx
            if switch_tok_idx > 0:
                prev_tok = tokens_in_line[switch_tok_idx - 1]
                curr_tok = tokens_in_line[switch_tok_idx]
                dist = get_bounding_box(curr_tok)[0] - get_bounding_box(prev_tok)[2]
                prev_txt = str(prev_tok.get("text", "")).strip()
                if 0.0 <= dist <= 35.0 and re.search(r"^[>▶\+v▼\-]$", prev_txt):
                    start_idx = switch_tok_idx - 1

            # Build joined header string strictly from start_idx onwards (sidebar completely excluded)
            header_tokens = tokens_in_line[start_idx:]
            joined_header = " ".join(str(t.get("text", "")).strip() for t in header_tokens)

            # Match: "Standard Switch: name", "Distributed Switch: name", or "Switch: name"
            m = re.search(r"(?:Standard\s*Switch|Distributed\s*Switch|\bSwitch)\s*[:;\s]\s*([A-Za-z0-9_\-\.\s]+?)(?:\s+(?:ADD|EDIT|MANAGE|\.\.\.)|$)", joined_header, re.I)
            if m:
                raw_sw_name = m.group(1).strip()
                raw_sw_name = re.split(r"\s+(?:ADD|EDIT|MANAGE|\.\.\.)", raw_sw_name, flags=re.I)[0].strip()
                sw_name = apply_normalizations(raw_sw_name, normalizations)

                is_collapsed = bool(re.search(r"^[>▶\+]", joined_header))
                header_x0 = get_bounding_box(header_tokens[0])[0]

                switch_headers.append({
                    "name": sw_name,
                    "y_top": line_item["y"],
                    "x_left": header_x0,
                    "collapsed": is_collapsed
                })

        switch_headers.sort(key=lambda s: s["y_top"])
        if not switch_headers:
            return []

        # Dynamic Sidebar Boundary (0px for cropped screenshots, ~175px for full screen)
        min_switch_x = min(sh["x_left"] for sh in switch_headers)
        sidebar_cutoff_x = max(0.0, min_switch_x - 15.0)

        for idx, sh in enumerate(switch_headers):
            sh["y_start"] = sh["y_top"]
            if idx + 1 < len(switch_headers):
                sh["y_end"] = switch_headers[idx + 1]["y_top"]
            else:
                sh["y_end"] = float('inf')

        # -------------------------------------------------------------
        # Step 2: Extract Records per Switch Band
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
                if not (sh["y_start"] + 15.0 <= by0 < sh["y_end"]):
                    continue

                if noise_re and noise_re.search(text):
                    continue
                if re.match(r"^(?:ADD\s*NETWORKING|REFRESH|EDIT|MANAGE\s*PHYSICAL|ACTIONS|\.\.\.)$", text, re.I):
                    continue
                if re.search(r"(?:Standard|Distributed)\s*Switch:", text, re.I):
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

            # Dynamic Physical Column Anchor Detection
            phys_anchor_x = None
            for t in inner_tokens:
                txt = t.get("text", "").strip()
                if re.search(r"(?:Physical\s*Adapters|.*-DVUplinks-.*|\bvmnic\d+\b|No\s*physical\s*network\s*adapters)", txt, re.I):
                    x0, _, _, _ = get_bounding_box(t)
                    if phys_anchor_x is None or x0 < phys_anchor_x:
                        phys_anchor_x = x0

            if phys_anchor_x is not None and phys_anchor_x > (sidebar_cutoff_x + 80.0):
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < (phys_anchor_x - 10.0)]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= (phys_anchor_x - 15.0)]
            else:
                x_min_band = min(get_bounding_box(t)[0] for t in inner_tokens)
                x_max_band = max(get_bounding_box(t)[2] for t in inner_tokens)
                x_mid_band = x_min_band + (x_max_band - x_min_band) * 0.48
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < x_mid_band]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= x_mid_band]

            # Physical Column Extraction
            physical_records = []
            vmnic_matches = []
            for pt in physical_tokens:
                txt = pt.get("text", "").strip()
                if re.search(r"\(0\s*NIC\s*Adapters\)", txt, re.I):
                    continue
                m = re.search(r"(?:^|[\s\b])([xX!\*\-\+]\s*)?\b(vmnic\d+|eno\d+|ens\d+|eth\d+|bond\d+)\b(?:\s*(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000))?", txt, re.I)
                if m:
                    status_prefix = m.group(1) or ""
                    nic_id = m.group(2).lower()
                    speed_str = m.group(3) or ""
                    is_standby = bool(re.search(r"[xX!]", status_prefix)) or bool(re.search(r"\bStandby\b", txt, re.I))
                    vmnic_matches.append({
                        "nic": nic_id,
                        "speed": speed_str,
                        "standby": is_standby,
                        "y": get_bounding_box(pt)[1]
                    })

            seen_nics = set()
            for vm in sorted(vmnic_matches, key=lambda x: x["y"]):
                if vm["nic"] in seen_nics:
                    continue
                seen_nics.add(vm["nic"])

                nic_speed = vm["speed"]
                if not nic_speed:
                    for pt in physical_tokens:
                        if abs(get_bounding_box(pt)[1] - vm["y"]) < 20.0:
                            sm = re.search(r"(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000)", pt.get("text", ""), re.I)
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

            # Logical Column Extraction (Line-Clustered)
            logical_lines = cluster_tokens_into_lines(logical_tokens, y_thresh=8.0)

            vmk_entries = []
            for line_item in logical_lines:
                txt = line_item["text"].strip()
                m_vmk = re.search(r"\b(vmk[oO0-9]+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt, re.I)
                if m_vmk:
                    clean_vmk = re.sub(r"(?i)vmk[oO]", "vmk0", m_vmk.group(1)).lower()
                    vmk_entries.append({
                        "vmk": clean_vmk,
                        "ip": m_vmk.group(2).strip(),
                        "y": line_item["y"]
                    })
                else:
                    m_ip = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt)
                    if m_ip:
                        ip_val = m_ip.group(1).strip()
                        y_ip = line_item["y"]
                        found_vmk = None
                        for line_item2 in logical_lines:
                            if abs(line_item2["y"] - y_ip) < 20.0:
                                m_v = re.search(r"\b(vmk[oO0-9]+)\b", line_item2["text"], re.I)
                                if m_v:
                                    found_vmk = re.sub(r"(?i)vmk[oO]", "vmk0", m_v.group(1)).lower()
                                    break
                        if found_vmk:
                            vmk_entries.append({
                                "vmk": found_vmk,
                                "ip": ip_val,
                                "y": y_ip
                            })

            service_titles = []
            for line_item in logical_lines:
                txt = line_item["text"].strip()
                if re.match(r"^(?:[vV✓✔\-\s]*(?:VLAN\s*ID|VMkernel\s*Ports|Virtual\s*Machines)|vmk[oO0-9]+|No\s*associated)", txt, re.I):
                    continue
                if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", txt):
                    continue
                if re.search(r"(?:Standard|Distributed)\s*Switch:", txt, re.I):
                    continue
                if noise_re and noise_re.search(txt):
                    continue

                cleaned_title = re.sub(r"[\s\.\…\×\<\>\(\)]+$", "", txt).strip()
                cleaned_title = apply_normalizations(cleaned_title, normalizations)

                if len(cleaned_title) <= 2:
                    continue

                service_titles.append({
                    "title": cleaned_title,
                    "y": line_item["y"]
                })

            service_titles.sort(key=lambda s: s["y"])

            assigned_titles = set()
            for v in vmk_entries:
                candidates = [st for st in service_titles if 0 <= (v["y"] - st["y"]) <= 150.0]
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
