"""
Pure Python Deterministic Geometric Topology Extractor (v4.7.0)
Extracts virtual switches, physical adapters, and logical network services
directly from RapidOCR bounding box coordinates without calling any LLM.
"""

import re
from typing import List, Dict, Any, Tuple


def extract_topology_from_ocr_boxes(
    raw_tokens: List[Dict[str, Any]],
    image_width: int = 1920,
    image_height: int = 1080,
) -> List[Dict[str, Any]]:
    if not raw_tokens:
        return []

    # -------------------------------------------------------------
    # Step 1: Pre-crop Left Navigation Sidebar & Global Header
    # -------------------------------------------------------------
    anchor_x = None
    anchor_y = None
    for t in raw_tokens:
        text = str(t.get("text", "")).strip()
        if re.search(
            r"(?:Virtual\s*switches|(?:Standard|Distributed)\s*Switch:)",
            text,
            re.I,
        ):
            box = t.get(
                "box", [[0, 0], [0, 0], [0, 0], [0, 0]]
            )
            bx = min(p[0] for p in box)
            by = min(p[1] for p in box)
            if anchor_x is None or bx < anchor_x:
                anchor_x = bx
            if anchor_y is None or by < anchor_y:
                anchor_y = by

    if anchor_x is None:
        anchor_x = 220.0
    if anchor_y is None:
        anchor_y = 50.0

    filtered_tokens = []
    for t in raw_tokens:
        box = t.get("box", [[0, 0], [0, 0], [0, 0], [0, 0]])
        x_min = min(p[0] for p in box)
        y_min = min(p[1] for p in box)
        text = str(t.get("text", "")).strip()

        if x_min < (anchor_x - 15.0):
            continue
        if y_min < (anchor_y - 15.0):
            continue
        if re.match(
            r"^(?:ADD\s*NETWORKING|REFRESH|EDIT|MANAGE\s*PHYSICAL|ACTIONS|\.\.\.)$",
            text,
            re.I,
        ):
            continue
        filtered_tokens.append(t)

    # -------------------------------------------------------------
    # Step 2: Vertical Y-Slicing by Switch Headers
    # -------------------------------------------------------------
    switch_headers = []
    for t in filtered_tokens:
        text = str(t.get("text", "")).strip()
        m = re.search(
            r"^(?:>|▶|v|▼|\+|-)?\s*(?:Standard\s*Switch|Distributed\s*Switch):\s*([^\n\r]+)",
            text,
            re.I,
        )
        if m:
            box = t.get("box", [[0, 0], [0, 0], [0, 0], [0, 0]])
            y_top = min(p[1] for p in box)
            sw_name = m.group(1).strip()
            sw_name = re.sub(r"\s+", " ", sw_name)
            sw_name = re.sub(r"(?i)(switch)o(\d*)", r"\g<1>0\2", sw_name)
            sw_name = re.split(
                r"\s+(?:ADD|EDIT|MANAGE|\.\.\.)",
                sw_name,
                flags=re.I,
            )[0].strip()

            is_collapsed = bool(re.match(r"^(?:>|▶|\+)", text.strip()))
            switch_headers.append({
                "name": sw_name,
                "y_top": y_top,
                "collapsed": is_collapsed,
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
    # Step 3: Parse Each Switch Band Independently
    # -------------------------------------------------------------
    extracted_records: List[Dict[str, Any]] = []

    for sh in switch_headers:
        sw_name = sh["name"]
        band_tokens = [
            t
            for t in filtered_tokens
            if sh["y_start"] <= min(p[1] for p in t["box"]) < sh["y_end"]
        ]

        inner_tokens = [
            t
            for t in band_tokens
            if min(p[1] for p in t["box"]) > (sh["y_start"] + 30.0)
        ]
        if not inner_tokens:
            extracted_records.append({
                "switch": sw_name,
                "interface": "No Uplink",
                "type": "Uplink",
                "ip": "",
                "purpose": "",
                "role": "None",
                "speed": "",
            })
            continue

        x_min_band = min(min(p[0] for p in t["box"]) for t in inner_tokens)
        x_max_band = max(max(p[0] for p in t["box"]) for t in inner_tokens)
        x_mid_band = x_min_band + (x_max_band - x_min_band) * 0.48

        logical_tokens = [
            t for t in inner_tokens if min(p[0] for p in t["box"]) < x_mid_band
        ]
        physical_tokens = [
            t for t in inner_tokens if min(p[0] for p in t["box"]) >= x_mid_band
        ]

        # ---------------------------------------------------------
        # Physical Column Extraction (Right Column)
        # ---------------------------------------------------------
        physical_records = []
        phys_text_blob = " ".join(t["text"] for t in physical_tokens)

        has_no_adapters = bool(
            re.search(r"No\s*physical\s*network\s*adapters", phys_text_blob, re.I)
        )

        vmnic_matches = []
        for pt in physical_tokens:
            txt = pt["text"].strip()
            # Ignore 0 NIC Adapters placeholder slots in vDS
            if re.search(r"\(0\s*NIC\s*Adapters\)", txt, re.I):
                continue
            m = re.search(
                r"(?:[xX!\*\-\+]\s*)?\b(vmnic\d+)\b"
                r"(?:\s*(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000))?",
                txt,
                re.I,
            )
            if m:
                nic_id = m.group(1).lower()
                speed_str = m.group(2) or ""
                is_standby = bool(re.search(r"(?:^[xX!]\s*|Standby)", txt, re.I))
                vmnic_matches.append({
                    "nic": nic_id,
                    "speed": speed_str,
                    "standby": is_standby,
                    "y": min(p[1] for p in pt["box"]),
                })

        seen_nics = set()
        for vm in sorted(vmnic_matches, key=lambda x: x["y"]):
            if vm["nic"] in seen_nics:
                continue
            seen_nics.add(vm["nic"])

            nic_speed = vm["speed"]
            if not nic_speed:
                for pt in physical_tokens:
                    if (
                        abs(min(p[1] for p in pt["box"]) - vm["y"]) < 15.0
                    ):
                        sm = re.search(
                            r"(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000)",
                            pt["text"],
                            re.I,
                        )
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
                "speed": nic_speed,
            })

        if not physical_records:
            physical_records.append({
                "switch": sw_name,
                "interface": "No Uplink",
                "type": "Uplink",
                "ip": "",
                "purpose": "",
                "role": "None",
                "speed": "",
            })

        extracted_records.extend(physical_records)

        # ---------------------------------------------------------
        # Logical Column Extraction (Left Column)
        # ---------------------------------------------------------
        vmk_entries = []
        for lt in logical_tokens:
            txt = lt["text"].strip()
            m_vmk = re.search(
                r"\b(vmk\d+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
                txt,
                re.I,
            )
            if m_vmk:
                vmk_entries.append({
                    "vmk": m_vmk.group(1).lower(),
                    "ip": m_vmk.group(2).strip(),
                    "y": min(p[1] for p in lt["box"]),
                })
            else:
                m_ip = re.search(
                    r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt
                )
                if m_ip:
                    ip_val = m_ip.group(1).strip()
                    y_ip = min(p[1] for p in lt["box"])
                    found_vmk = None
                    for lt2 in logical_tokens:
                        if (
                            abs(min(p[1] for p in lt2["box"]) - y_ip) < 15.0
                        ):
                            m_v = re.search(r"\b(vmk\d+)\b", lt2["text"], re.I)
                            if m_v:
                                found_vmk = m_v.group(1).lower()
                                break
                    if found_vmk:
                        vmk_entries.append({
                            "vmk": found_vmk,
                            "ip": ip_val,
                            "y": y_ip,
                        })

        service_titles = []
        for lt in logical_tokens:
            txt = lt["text"].strip()
            if re.match(
                r"^(?:VLAN\s*ID|VMkernel\s*Ports|Virtual\s*Machines|vmk\d+|No\s*associated)",
                txt,
                re.I,
            ):
                continue
            if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", txt):
                continue
            if len(txt) <= 2:
                continue

            box = lt.get("box", [[0, 0], [0, 0], [0, 0], [0, 0]])
            y_box = min(p[1] for p in box)
            service_titles.append({
                "title": txt,
                "y": y_box,
            })

        service_titles.sort(key=lambda s: s["y"])

        assigned_titles = set()
        for v in vmk_entries:
            candidates = [
                st for st in service_titles
                if 0 <= (v["y"] - st["y"]) <= 85.0
            ]
            if candidates:
                candidates.sort(key=lambda c: v["y"] - c["y"])
                purpose_name = candidates[0]["title"]
                assigned_titles.add(purpose_name)
            else:
                purpose_name = (
                    "Management Network"
                    if v["vmk"] == "vmk0"
                    else v["vmk"]
                )

            extracted_records.append({
                "switch": sw_name,
                "interface": v["vmk"],
                "type": "VMkernel",
                "ip": v["ip"],
                "purpose": purpose_name,
                "role": "None",
                "speed": "",
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
                "speed": "",
            })

    return extracted_records
