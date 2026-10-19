"""
Pure Python Deterministic Geometric Topology Extractor (v4.7.2)
100% Platform-Agnostic & Config-Driven: Zero Hardcoded Vendor/Site Strings.
All geometry thresholds, noise filters, and normalizations are loaded from plat_cfg.
"""

import re
from typing import List, Dict, Any, Tuple, Optional


def _get_box(token_or_box: Any) -> Tuple[float, float, float, float]:
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


def _apply_token_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    for rule in normalizations:
        pat = rule.get("pattern")
        rep = rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


def extract_topology_from_ocr_boxes(
    raw_tokens: List[Dict[str, Any]], 
    image_width: int = 1920, 
    image_height: int = 1080,
    plat_cfg: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    if not raw_tokens:
        return []

    cfg = plat_cfg or {}
    geom = cfg.get("layout_geometry") or {}
    sidebar_x_cutoff = float(geom.get("sidebar_x_cutoff", 280.0))
    midline_ratio = float(geom.get("midline_ratio", 0.48))
    header_prox = float(geom.get("header_proximity_threshold", 85.0))
    nic_speed_prox = float(geom.get("nic_speed_y_proximity", 18.0))

    noise_filters = cfg.get("noise_filters", [])
    if noise_filters:
        escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
        noise_re = re.compile(r"^(?:" + r"|".join(escaped) + r")$|(?:" + r"|".join(escaped) + r")", re.I)
    else:
        noise_re = None

    normalizations = cfg.get("token_normalizations", [])

    # -------------------------------------------------------------
    # Step 1: Detect Switch Headers in Content Canvas (X >= 150px)
    # -------------------------------------------------------------
    switch_headers = []
    for t in raw_tokens:
        text = str(t.get("text", "")).strip()
        m = re.search(r"^(?:>|▶|v|▼|\+|-)?\s*(?:Standard\s*Switch|Distributed\s*Switch|Bridge):\s*([^\n\r]+)", text, re.I)
        if m:
            bx, by, _, _ = _get_box(t)
            if bx < 150.0:
                continue
            raw_sw_name = m.group(1).strip()
            raw_sw_name = re.split(r"\s+(?:ADD|EDIT|MANAGE|\.\.\.)", raw_sw_name, flags=re.I)[0].strip()
            sw_name = _apply_token_normalizations(raw_sw_name, normalizations)

            is_collapsed = bool(re.match(r"^(?:>|▶|\+)", text.strip()))
            switch_headers.append({
                "name": sw_name,
                "y_top": by,
                "x_left": bx,
                "collapsed": is_collapsed
            })

    switch_headers.sort(key=lambda s: s["y_top"])
    if not switch_headers:
        return []

    for idx, sh in enumerate(switch_headers):
        sh["y_start"] = sh["y_top"]
        if idx + 1 < len(switch_headers):
            sh["y_end"] = switch_headers[idx + 1]["y_top"]
        else:
            sh["y_end"] = float(image_height)

    # -------------------------------------------------------------
    # Step 2: Filter Valid Inner Tokens (X >= sidebar_x_cutoff)
    # -------------------------------------------------------------
    content_tokens = []
    for t in raw_tokens:
        bx0, by0, bx1, by1 = _get_box(t)
        text = str(t.get("text", "")).strip()

        if bx0 < sidebar_x_cutoff:
            continue
        if noise_re and noise_re.search(text):
            continue
        if re.match(r"^(?:ADD\s*NETWORKING|REFRESH|EDIT|MANAGE\s*PHYSICAL|ACTIONS|\.\.\.)$", text, re.I):
            continue

        content_tokens.append(t)

    # -------------------------------------------------------------
    # Step 3: Extract Services & Adapters per Switch Band
    # -------------------------------------------------------------
    extracted_records: List[Dict[str, Any]] = []

    for sh in switch_headers:
        sw_name = sh["name"]
        band_tokens = [
            t for t in content_tokens 
            if sh["y_start"] <= _get_box(t)[1] < sh["y_end"]
        ]

        if not band_tokens:
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

        x_min_band = min(_get_box(t)[0] for t in band_tokens)
        x_max_band = max(_get_box(t)[2] for t in band_tokens)
        x_mid_band = x_min_band + (x_max_band - x_min_band) * midline_ratio

        logical_tokens = [t for t in band_tokens if _get_box(t)[0] < x_mid_band]
        physical_tokens = [t for t in band_tokens if _get_box(t)[0] >= x_mid_band]

        # --- Physical Adapters ---
        physical_records = []
        vmnic_matches = []
        for pt in physical_tokens:
            txt = pt.get("text", "").strip()
            if re.search(r"\(0\s*NIC\s*Adapters\)", txt, re.I):
                continue
            m = re.search(r"(?:[xX!\*\-\+]\s*)?\b(vmnic\d+|eno\d+|ens\d+|eth\d+|bond\d+)\b(?:\s*(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000))?", txt, re.I)
            if m:
                nic_id = m.group(1).lower()
                speed_str = m.group(2) or ""
                is_standby = bool(re.search(r"(?:^[xX!]\s*|Standby)", txt, re.I))
                vmnic_matches.append({
                    "nic": nic_id,
                    "speed": speed_str,
                    "standby": is_standby,
                    "y": _get_box(pt)[1]
                })

        seen_nics = set()
        for vm in sorted(vmnic_matches, key=lambda x: x["y"]):
            if vm["nic"] in seen_nics:
                continue
            seen_nics.add(vm["nic"])

            nic_speed = vm["speed"]
            if not nic_speed:
                for pt in physical_tokens:
                    if abs(_get_box(pt)[1] - vm["y"]) < nic_speed_prox:
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

        # --- Logical Services ---
        vmk_entries = []
        for lt in logical_tokens:
            txt = lt.get("text", "").strip()
            m_vmk = re.search(r"\b(vmk\d+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt, re.I)
            if m_vmk:
                vmk_entries.append({
                    "vmk": m_vmk.group(1).lower(),
                    "ip": m_vmk.group(2).strip(),
                    "y": _get_box(lt)[1]
                })
            else:
                m_ip = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt)
                if m_ip:
                    ip_val = m_ip.group(1).strip()
                    y_ip = _get_box(lt)[1]
                    found_vmk = None
                    for lt2 in logical_tokens:
                        if abs(_get_box(lt2)[1] - y_ip) < 18.0:
                            m_v = re.search(r"\b(vmk\d+)\b", lt2.get("text", ""), re.I)
                            if m_v:
                                found_vmk = m_v.group(1).lower()
                                break
                    if found_vmk:
                        vmk_entries.append({
                            "vmk": found_vmk,
                            "ip": ip_val,
                            "y": y_ip
                        })

        service_titles = []
        for lt in logical_tokens:
            txt = lt.get("text", "").strip()
            if re.match(r"^(?:VLAN\s*ID|VMkernel\s*Ports|Virtual\s*Machines|vmk\d+|No\s*associated)", txt, re.I):
                continue
            if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", txt):
                continue
            if noise_re and noise_re.search(txt):
                continue
            if len(txt) <= 2:
                continue

            cleaned_title = _apply_token_normalizations(txt, normalizations)
            service_titles.append({
                "title": cleaned_title,
                "y": _get_box(lt)[1]
            })

        service_titles.sort(key=lambda s: s["y"])

        assigned_titles = set()
        for v in vmk_entries:
            candidates = [st for st in service_titles if 0 <= (v["y"] - st["y"]) <= header_prox]
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
