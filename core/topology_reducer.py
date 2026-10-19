"""
Topology State Reducer — Single Authoritative State Reducer (v4.6.2)

Implements reduce_topology_pipeline() as the unified deduplication,
prefix normalization, and expanded-state precedence engine for all
inference paths (hybrid stream and legacy heuristic).

v4.6.2 changes:
  - Sidebar artifact rejection: drop records matching Storage Adapters,
    Agent VM Settings, PR Spain Fuenmayor Age VLab, PR Spain Fuenmayor VLab.
  - Switch name typo normalization: vSwitcho -> vSwitch0, vSwitcho1 -> vSwitch01
  - Ghost header rejection: collapsed headers, UI control strings, site names
  - Expanded state precedence: auto-purge dummy "No Uplink" records when real adapters exist
  - Strict 5-tuple deduplication
  - VMkernel/Port Group disambiguation: no redundant Port Group when VMkernel with same purpose exists
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple


def reduce_topology_pipeline(
    raw_records: List[Dict[str, Any]],
    plat_config: Dict[str, Any],
    platform_tokens: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Reduce raw parsed topology records through a single authoritative pipeline.

    v4.6.2 strict normalization & precedence rules:
      1. Switch Name Typo & Prefix Normalization
      2. Sidebar & UI Artifact Rejection
      3. Expanded State Precedence
      4. Deduplication & VMkernel/Port Group Disambiguation
    """
    if not raw_records:
        return []

    allowed_types = plat_config.get("allowed_types", ["Uplink", "VMkernel", "Port Group"])
    role_options = plat_config.get("role_options", ["Active Uplink", "Standby Uplink", "None"])
    default_role = plat_config.get("default_connected_role", "Active Uplink")
    sentinel_no_adapter = plat_config.get("sentinel_no_adapter", "No Uplink")

    uplink_pfx = str(platform_tokens.get("uplink_prefix", "vmnic")).lower()
    vmk_pfx = str(platform_tokens.get("vmk_prefix", "vmk")).lower()

    primary_uplink_type = allowed_types[0] if allowed_types else "Uplink"
    primary_vmk_type = allowed_types[1] if len(allowed_types) > 1 else "VMkernel"
    primary_pg_type = allowed_types[2] if len(allowed_types) > 2 else "Port Group"

    cleaned: List[Dict[str, Any]] = []

    # v4.6.2: Sidebar and collapsed-artifact rejection patterns
    _sidebar_artifacts = {
        "storage adapters", "agent vm settings",
        "pr spain fuenmayor age vlab", "pr spain fuenmayor vlab",
    }
    _ghost_re = re.compile(
        r"^(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s*.+$",
        re.IGNORECASE,
    )
    _collapsed_re = re.compile(
        r"^(?:>|▶|\+)?\s*(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s+.+$",
        re.IGNORECASE,
    )
    _ui_control_re = re.compile(
        r"^(?:ADD\s*NETWORKING|EDIT|MANAGE\s*PHYSICAL\s*ADAPTERS?|MANAGE\s*ADAPTERS?|REFRESH|ACTIONS?|\.\.\.|Ver\s*\.+\||Ref|No physical network adapters|No Uplink)$",
        re.IGNORECASE,
    )

    for r in raw_records:
        if not isinstance(r, dict):
            continue
        item = {str(k).strip().lower(): v for k, v in r.items()}

        # 1. Switch name normalization & prefix stripping
        raw_sw = str(item.get("switch") or item.get("parent") or item.get("switch_name") or "").strip()
        sw_clean = re.sub(r"^(Standard\s*Switch|Distributed\s*Switch|Switch|Bridge):\s*", "", raw_sw, flags=re.I).strip()
        sw_clean = re.sub(r"\s+", " ", sw_clean).strip()
        # v4.6.1: normalize OCR letter 'o'/'O' → digit '0' in switch suffixes
        # Pattern: vSwitch + 'o'/'O' + digits -> vSwitch0 + digits
        # Also handle: vSwitch + 'o'/'O' alone -> vSwitch0
        sw_clean = re.sub(r"(?i)(vswitch)o(\d*)", r"\g<1>0\2", sw_clean)
        sw_clean = re.sub(r"(?i)(vswitch)o$", r"\g<1>0", sw_clean)
        if not sw_clean:
            continue

        # 2. Interface name cleanup
        raw_iface = str(item.get("interface") or item.get("port") or item.get("nic") or "").strip()
        clean_iface = re.sub(r"^[xX\-\*\>\s]+", "", raw_iface).strip()
        if clean_iface.lower() in ("none", "null", "no physical adapters", "no physical network adapters"):
            clean_iface = ""

        if re.match(r"^mk\d+$", clean_iface, re.I):
            clean_iface = f"v{clean_iface.lower()}"

        # 3. Dynamic Type Inference
        raw_type = str(item.get("type") or "").strip()
        norm_type = next((at for at in allowed_types if at.lower() in raw_type.lower()), "")
        if not norm_type:
            if clean_iface.lower().startswith(uplink_pfx) or item.get("role") in role_options:
                norm_type = primary_uplink_type
            elif clean_iface.lower().startswith(vmk_pfx) or item.get("ip"):
                norm_type = primary_vmk_type
            else:
                norm_type = primary_pg_type

        # 4. Strict Column Boundaries
        raw_ip = str(item.get("ip") or item.get("ipv4") or "").strip()
        raw_purpose = str(item.get("purpose") or item.get("service") or item.get("name") or "").strip()
        raw_role = str(item.get("role") or item.get("uplink_role") or "").strip()
        speed_val = str(item.get("speed") or "").strip()

        # v4.6.2: Reject sidebar/collapsed artifacts
        low_sw = raw_sw.lower().strip()
        if low_sw in _sidebar_artifacts:
            continue
        if _ghost_re.match(raw_sw) or _collapsed_re.match(raw_sw):
            continue
        if _ui_control_re.match(raw_sw) or _ui_control_re.match(raw_iface) or _ui_control_re.match(raw_purpose):
            continue

        speed_match = re.search(r"(\d+\s*(?:Gbit/s|Mbit/s|Kbit/s|Full|Half))", clean_iface, re.I)
        if speed_match:
            if not speed_val:
                speed_val = speed_match.group(1).strip()
            clean_iface = clean_iface.replace(speed_match.group(1), "").strip()

        if norm_type == primary_uplink_type:
            raw_ip = ""
            raw_purpose = ""
            if not clean_iface or clean_iface == sentinel_no_adapter:
                clean_iface = sentinel_no_adapter
                raw_role = "None"
            else:
                raw_role = raw_role if raw_role in role_options else default_role
        else:
            raw_role = "None"

        cleaned.append({
            "switch": sw_clean,
            "interface": clean_iface,
            "type": norm_type,
            "ip": raw_ip,
            "purpose": raw_purpose,
            "role": raw_role,
            "speed": speed_val,
        })

    # 5. Expanded State Precedence:
    # If switch has real physical NICs, eliminate placeholder "No Uplink" records
    switches_with_real_nics = {
        r["switch"] for r in cleaned
        if r["type"] == primary_uplink_type and r["interface"] and r["interface"] != sentinel_no_adapter
    }

    # 6. 5-Tuple Deduplication
    seen_sigs: Set[Tuple[str, str, str, str, str]] = set()
    deduped: List[Dict[str, Any]] = []

    for r in cleaned:
        if r["interface"] == sentinel_no_adapter and r["switch"] in switches_with_real_nics:
            continue

        sig = (
            r["switch"],
            r["interface"].lower(),
            r["type"],
            r["purpose"].lower(),
            r["ip"],
        )
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(r)

    # 7. Deduplicate VMkernel vs PortGroup with identical purpose on same switch
    vmk_purposes = {
        (r["switch"], r["purpose"].lower())
        for r in deduped if r.get("type") == primary_vmk_type and r.get("purpose")
    }
    final_records = [
        r for r in deduped
        if not (
            r.get("type") == primary_pg_type
            and (r.get("switch"), (r.get("purpose") or "").lower()) in vmk_purposes
        )
    ]

    return final_records
