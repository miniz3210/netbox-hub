#!/usr/bin/env bash
set -e

echo "=== 1. Update config/constants.py to v4.8.18 ==="
cat << 'INNER_EOF' > config/constants.py
"""
Application constants for NetBox Hub (v4.8.18)
"""
import os
from pathlib import Path

APP_NAME = "NetBox Hub"
APP_VERSION = "4.8.18"
PAGE_TITLE = "NetBox Hub"
PAGE_ICON = "📦"
DEFAULT_PLATFORM = "VMware ESXi"

GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
LOCAL_CACHE_DIR = os.getenv("LOCAL_CACHE_DIR", "data/devicetype-library")

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

RULES_FILE = os.getenv("RULES_FILE", "data/naming_rules.yaml")
HISTORY_FILE = os.getenv("HISTORY_FILE", "data/history.json")
BASELINE_FILE = os.getenv("BASELINE_FILE", "data/baseline_rules.yaml")
AUTOCORRECT_RULES_FILE = os.getenv("AUTOCORRECT_RULES_FILE", "data/autocorrect_rules.json")
VAULT_DB_FILE = os.getenv("VAULT_DB_FILE", "data/vault.db")

DEFAULT_VLAN_NAME_PATTERN = "<role>"
DEFAULT_PREFIX_PATTERN = "<site> <role> -- VLAN <vid>"

NETWORKING_ACRONYMS = {
    "VLAN", "IP", "IPv4", "IPv6", "VIP", "VM", "ESXi", "SAN", "NAS", "DMZ",
    "CCTV", "WAP", "AP", "FW", "RTR", "SW", "OOB", "MGMT", "VPN", "WAN",
    "LAN", "WLAN", "DNS", "DHCP", "NAT", "BGP", "OSPF", "VRF", "HCI",
    "NIC", "PoE", "LACP", "LAG", "MTU", "QoS", "PRW", "HSRP", "VRRP", "VSS", "VDS"
}

UI_NAV_NOISE = [
    "Virtual switches", "Physical Adapters", "Storage Adapters", "Licensing",
    "Host Profile", "Networking", "Storage", "System", "Hardware",
    "Configure", "Summary", "Monitor", "Permissions", "VMs", "Datastores",
    "Networks", "Updates", "ACTIONS", "ADD NETWORKING...", "REFRESH"
]
INNER_EOF

echo "=== 2. Rewrite core/extractors/esxi.py (Bulletproof Header Engine) ==="
cat << 'INNER_EOF' > core/extractors/esxi.py
"""
VMware ESXi & vDS Pure Python Geometric Topology Extractor (v4.8.18)
Universal, Deterministic, 100% Configuration-Driven.
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
        geom_cfg = cfg.get("geometry", {})
        y_thresh = float(geom_cfg.get("line_cluster_y_thresh", 8.0))
        max_vmk_dist = float(geom_cfg.get("vmk_to_title_max_dist", 160.0))

        if noise_filters:
            escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
            noise_re = re.compile(r"(?:" + r"|".join(escaped) + r")", re.I)
        else:
            noise_re = None

        header_y_thresh = max(18.0, y_thresh * 2)
        header_lines = cluster_tokens_into_lines(raw_tokens, y_thresh=header_y_thresh)
        switch_headers = []

        for line_item in header_lines:
            line_text = line_item["text"]

            # Bulletproof: Matches standalone 'Switch' or 'Bridge', optional colon, followed by name.
            # \b prevents matching inside 'dvSwitch0-DVUplinks'. OCR typos on 'Distributed' are ignored.
            m = re.search(r"\b(?:Switch|Bridge)\b\s*[:;]?\s+([A-Za-z0-9_\-\.]{3,})(?:\s+(?:ADD|EDIT|MANAGE|ACTIONS|\.\.\.)|$)", line_text, re.I)
            if not m:
                continue

            raw_sw_name = m.group(1).strip()
            sw_name = apply_normalizations(raw_sw_name, normalizations)

            tokens_in_line = line_item["tokens"]
            
            # Locate the exact token containing 'Switch'
            header_tok_idx = 0
            for idx, tok in enumerate(tokens_in_line):
                if re.search(r"\b(?:Switch|Bridge)\b", str(tok.get("text", "")).strip(), re.I):
                    header_tok_idx = idx
                    break

            # Smart backwards walking to find the true X coordinate of the container
            start_idx = header_tok_idx
            for i in range(header_tok_idx - 1, -1, -1):
                txt = str(tokens_in_line[i].get("text", "")).strip()
                dist = get_bounding_box(tokens_in_line[i+1])[0] - get_bounding_box(tokens_in_line[i])[2]
                
                # Stop if we hit a massive gap (likely crossed into the sidebar)
                if dist > 80.0:
                    break
                # Stop immediately if we hit a known sidebar keyword
                if re.search(r"(?:Virtual|Networking|Storage|System|Hardware)", txt, re.I):
                    break
                
                start_idx = i
                
                # If we included a chevron, this is the definitive start of the header
                if re.search(r"^[>▶\+v▼\-]", txt):
                    break

            header_tokens = tokens_in_line[start_idx:]
            joined_header = " ".join(str(t.get("text", "")).strip() for t in header_tokens)
            is_collapsed = bool(re.search(r"^[>▶\+]", joined_header.strip()))
            header_x0 = get_bounding_box(tokens_in_line[start_idx])[0]

            switch_headers.append({
                "name": sw_name,
                "y_top": line_item["y"],
                "x_left": header_x0,
                "collapsed": is_collapsed
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
                sh["y_end"] = float('inf')

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
                if re.search(r"\b(?:Standard|Distributed)\s*Switch\b", text, re.I):
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

            phys_anchor_x = None
            for t in inner_tokens:
                txt = t.get("text", "").strip()
                if re.search(r"(?:Physical\s*Adapters|DVUplink|vm[nN][kKiI1lL]c\s*\d+|No\s*physical\s*network\s*adapters)", txt, re.I):
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

            physical_records = []
            vmnic_matches = []

            for pt in physical_tokens:
                txt = pt.get("text", "").strip()
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
                            otxt = other_pt.get("text", "").strip()
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

            logical_lines = cluster_tokens_into_lines(logical_tokens, y_thresh=y_thresh)

            vmk_entries = []
            for line_item in logical_lines:
                txt = line_item["text"].strip()
                m_vmk = re.search(r"\b(vmk\s*[oO0-9]+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt, re.I)
                if m_vmk:
                    clean_vmk = re.sub(r"\s+", "", m_vmk.group(1))
                    clean_vmk = re.sub(r"(?i)vmk[oO]", "vmk0", clean_vmk).lower()
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
                                m_v = re.search(r"\b(vmk\s*[oO0-9]+)\b", line_item2["text"], re.I)
                                if m_v:
                                    raw_v = re.sub(r"\s+", "", m_v.group(1))
                                    found_vmk = re.sub(r"(?i)vmk[oO]", "vmk0", raw_v).lower()
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

                if re.search(r"(?:vmk[eao]rn[eao]l|vmk[eao]rm[eao]l)\s*ports?", txt, re.I):
                    continue
                if re.search(r"virt\s*ual\s*machines?|machines?\s*\(\d+\)", txt, re.I):
                    continue
                if re.search(r"\bVLAN\s*ID\b", txt, re.I):
                    continue
                if re.search(r"\bvmk\s*[oO0-9]+\b", txt, re.I):
                    continue
                if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", txt):
                    continue
                if re.search(r"\b(?:Standard|Distributed)\s*Switch:", txt, re.I):
                    continue
                if re.search(r"No\s*associated\s*port\s*groups", txt, re.I):
                    continue
                if re.search(r"[^\x00-\x7F]", txt):
                    continue
                if re.search(r"(?i)sauqsey", txt):
                    continue
                if noise_re and noise_re.search(txt):
                    continue

                cleaned_title = re.sub(r"^[>\s\(\)\[\]\{\}\<\>▼▶✓✔\-\—\.\:\;]+", "", txt).strip()
                cleaned_title = re.sub(r"[\s\.\…\×\<\>\(\)\[\]\{\}]+$", "", cleaned_title).strip()
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
INNER_EOF

echo "=== 3. Force Flush YAML Version Headers ==="
python3 -c '
import re, os
p = "data/naming_rules.yaml"
if os.path.exists(p):
    with open(p, "r", encoding="utf-8") as f:
        c = f.read()
    c = re.sub(r"v4\.8\.\d+", "v4.8.18", c)
    with open(p, "w", encoding="utf-8") as f:
        f.write(c)
'

echo "=== 4. Rebuild Docker Image & Restart ==="
docker compose build --no-cache
docker compose up -d --force-recreate

echo "✅ ALL DONE! Refresh your browser. It MUST say v4.8.18."
