#!/usr/bin/env bash
set -e

echo "=== 1. Update config/constants.py to v4.8.24 ==="
cat << 'INNER_EOF' > config/constants.py
"""
Application constants for NetBox Hub (v4.8.24)
"""
import os
from pathlib import Path

APP_NAME = "NetBox Hub"
APP_VERSION = "4.8.24"
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
RULES_HISTORY_FILE = os.getenv("RULES_HISTORY_FILE", "data/history.json")
HISTORY_FILE = RULES_HISTORY_FILE
MAX_HISTORY_ENTRIES = int(os.getenv("MAX_HISTORY_ENTRIES", "50"))
BASELINE_FILE = os.getenv("BASELINE_FILE", "data/baseline_rules.yaml")
AUTOCORRECT_RULES_FILE = os.getenv("AUTOCORRECT_RULES_FILE", "data/autocorrect_rules.json")

VAULT_DB_FILE = os.getenv("VAULT_DB_FILE", "data/vault.db")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/database.db")

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

echo "=== 2. Rewrite core/extractors/esxi.py (v4.8.24 Card-Top Invariant & IP Healing) ==="
cat << 'INNER_EOF' > core/extractors/esxi.py
"""
VMware ESXi & vDS Universal Topology Extractor (v4.8.24)
Core Guarantees:
1. Strict Left-Sidebar Isolation & Right-Column Physical Adapter Separation.
2. Card-Top Title Invariant: In ESXi, every Port Group card contains a 'VLAN ID' row,
   and the Port Group title is strictly the single line immediately above 'VLAN ID'.
   Everything below 'VLAN ID' (VM counts, upside-down OCR hallucinations like 'sauye len',
   or raw vmk lines) is structurally blocked from ever becoming a Port Group title.
3. Resilient VMkernel & IPv4 Healing: Repairs OCR-corrupted VMkernel lines such as
   'vmkd : 192.158.4 0.135' -> vmk1 (192.158.40.135) and '192.16840.167' -> 192.168.40.167.
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


def heal_ipv4(raw_ip_str: str) -> Optional[str]:
    """Heal common low-resolution OCR IPv4 artifacts (spaces inside digits, merged octets)."""
    # Remove spaces between digits or around dots
    s = re.sub(r"(?<=[0-9\.])\s+(?=[0-9\.])", "", raw_ip_str)
    m_cand = re.search(r"(\d[\d\.]{6,16}\d)", s)
    if not m_cand:
        return None
    cand = m_cand.group(1)

    parts = [p for p in cand.split(".") if p != ""]
    # If 3 parts and one part has 4-6 digits (merged dot, e.g. '192.16840.167' -> '192.168.40.167')
    if len(parts) == 3:
        new_parts = []
        for p in parts:
            if len(p) in (4, 5, 6) and len(new_parts) + (3 - len(new_parts)) <= 4:
                p1, p2 = p[:3], p[3:]
                if int(p1) <= 255 and int(p2) <= 255:
                    new_parts.extend([p1, p2])
                else:
                    p1, p2 = p[:2], p[2:]
                    new_parts.extend([p1, p2])
            else:
                new_parts.append(p)
        parts = new_parts

    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return ".".join(str(int(p)) for p in parts)
    return None


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

        if noise_filters:
            escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
            noise_re = re.compile(r"^(?:" + r"|".join(escaped) + r")$", re.I)
        else:
            noise_re = None

        # -------------------------------------------------------------
        # Step 1: Detect Switch Headers & Isolate Left Navigation Sidebar
        # -------------------------------------------------------------
        header_segments = cluster_tokens_into_lines(raw_tokens, y_thresh=14.0, max_x_gap=45.0)
        switch_headers = []

        for seg in header_segments:
            line_text = seg["text"]

            if re.search(r"DVUplink|Virtual\s*switches", line_text, re.I):
                continue

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

            kw_x0 = seg["x"]
            for tok in seg["tokens"]:
                ttxt = str(tok.get("text", "")).strip()
                if re.search(r"(?:Standard|Distributed|Dist[a-z]*|Stand[a-z]*|Sw[i1l!t]+ch|Bridge)", ttxt, re.I):
                    kw_x0 = max(0.0, get_bounding_box(tok)[0] - 22.0)
                    break

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

            logical_card_x_positions = [
                get_bounding_box(t)[0] for t in inner_tokens
                if re.search(r"(?:V[L1I]AN|Virt\s*ual\s*Mach|VMk[a-z]*\s*Port|vmk\s*[oO0-9a-zA-Z])", str(t.get("text", "")), re.I)
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
                logical_tokens = inner_tokens
                physical_tokens = []

            # ---------------------------------------------------------
            # A. Physical Uplink Extraction
            # ---------------------------------------------------------
            physical_records = []
            vmnic_matches = []
            phys_lines = cluster_tokens_into_lines(physical_tokens, y_thresh=8.0, max_x_gap=80.0)

            for pline in phys_lines:
                txt = pline["text"].strip()
                if re.search(r"\(0\s*NIC\s*Adapters\)|No\s*physical\s*network", txt, re.I):
                    continue
                if re.search(r"DVUplink", txt, re.I) and not re.search(r"\bvm[nN]", txt, re.I):
                    continue

                txt_spaced = re.sub(r"([0-9lI|])(100000|40000|25000|10000|1000|100)\b", r"\1 \2", txt)
                txt_clean = re.sub(r"\b(?:100000|40000|25000|10000|1000|100)\s*(?:Gbit/s|Mbit/s|Full|Half)?\b", " ", txt_spaced, flags=re.I)

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
            # B. Card-Structured Logical Extraction
            # ---------------------------------------------------------
            logical_lines = cluster_tokens_into_lines(logical_tokens, y_thresh=y_thresh, max_x_gap=65.0)

            # 1. Resilient VMkernel & IPv4 Extraction
            vmk_entries = []
            used_vmk_names = set()
            for line_item in logical_lines:
                txt = re.sub(r"[^\x20-\x7E]+", " ", line_item["text"]).strip()
                m_vmk = re.search(r"\b(vmk\s*[a-zA-Z0-9]{1,2})\s*[:;\s]\s*([0-9][0-9\.\s]{6,})", txt, re.I)
                if m_vmk:
                    healed_ip = heal_ipv4(m_vmk.group(2))
                    if healed_ip:
                        raw_v = re.sub(r"\s+", "", m_vmk.group(1)).lower()
                        suffix = raw_v[3:]
                        if suffix in ("o", "0"):
                            clean_vmk = "vmk0"
                        elif suffix in ("1", "l", "i", "d", "j"):
                            clean_vmk = "vmk1"
                        elif suffix in ("2", "z"):
                            clean_vmk = "vmk2"
                        elif suffix in ("3",):
                            clean_vmk = "vmk3"
                        elif suffix.isdigit():
                            clean_vmk = f"vmk{suffix}"
                        else:
                            idx_num = 1
                            while f"vmk{idx_num}" in used_vmk_names:
                                idx_num += 1
                            clean_vmk = f"vmk{idx_num}"
                        used_vmk_names.add(clean_vmk)
                        vmk_entries.append({
                            "vmk": clean_vmk,
                            "ip": healed_ip,
                            "y": line_item["y"]
                        })

            # 2. Identify all 'VLAN ID' rows (1 per Card)
            vlan_rows = [
                l_item for l_item in logical_lines
                if re.search(r"(?:V[L1I\|]?A?N\s*[I1l\|]?D|(?<!\w)ID\s*[:;]\s*(?:\d+|\-+))", l_item["text"], re.I)
            ]

            def clean_card_title(raw_str: str) -> str:
                t = re.sub(r"[^\x20-\x7E]+", " ", raw_str).strip()
                t = re.sub(r"^(?:[>\(\)\[\]\{\}\<\>\-\.\:\;\|\+\*]+|[vV]\s+)\s*", "", t).strip()
                t = re.sub(r"[\s\.\×\<\>\(\)\[\]\{\}\|\-\:\;]+$", "", t).strip()
                t = re.sub(r"(?i)\bVan_(\d+)", r"Vlan_\1", t)
                return apply_normalizations(t, normalizations)

            service_titles = []
            if vlan_rows:
                # Strict Card-Top Rule: For each VLAN ID row, grab ONLY the single title line immediately above it
                for v_idx, v_row in enumerate(vlan_rows):
                    vy = v_row["y"]
                    prev_vy = vlan_rows[v_idx - 1]["y"] if v_idx > 0 else sh["y_start"]

                    candidates = []
                    for l_item in logical_lines:
                        ly = l_item["y"]
                        if not (prev_vy < ly < vy and 4.0 <= (vy - ly) <= 50.0):
                            continue
                        txt = re.sub(r"[^\x20-\x7E]+", " ", l_item["text"]).strip()
                        if re.search(r"\b(?:Sw[i1l!t]+ch|Bridge)\b\s*[:;]|ADD\s*NETWORKING|MANAGE\s*PHYS", txt, re.I):
                            continue
                        if re.search(r"(?:vmk[eao]rn|virt\s*ual\s*mach|machines?\s*\(|\bvmk\s*[a-zA-Z0-9]+\s*[:;])", txt, re.I):
                            continue
                        ct = clean_card_title(txt)
                        if len(ct) > 2 and not (noise_re and noise_re.match(ct)):
                            candidates.append((vy - ly, ct, ly))

                    if candidates:
                        candidates.sort(key=lambda x: x[0])
                        _, best_title, best_y = candidates[0]
                        service_titles.append({
                            "title": best_title,
                            "y": best_y,
                            "vlan_y": vy
                        })

            service_titles.sort(key=lambda s: s["y"])

            # 3. Bind each VMkernel entry to its enclosing Card (immediately above v["y"])
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

echo "✅ Patch v4.8.24 applied! Ready to Build."
