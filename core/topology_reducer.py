"""
Universal Topology Reducer (v4.7.0)
Applies state machine precedence, 5-tuple deduplication, and standards formatting.
"""

import re
from typing import List, Dict, Any, Set, Tuple


def reduce_topology_pipeline(
    raw_records: List[Dict[str, Any]],
    plat_config: Dict[str, Any],
    platform_tokens: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not raw_records:
        return []

    # 1. Expanded State Precedence:
    # Identify switches that possess real physical NICs
    switches_with_real_nics: Set[str] = set()
    for r in raw_records:
        sw = str(r.get("switch", "")).strip()
        iface = str(r.get("interface", "")).strip()
        rtype = str(r.get("type", "")).strip()
        if rtype == "Uplink" and iface and iface != "No Uplink":
            switches_with_real_nics.add(sw)

    # 2. Filter & Deduplicate
    seen_sigs: Set[Tuple[str, str, str, str, str]] = set()
    cleaned: List[Dict[str, Any]] = []

    for r in raw_records:
        sw = str(r.get("switch", "")).strip()
        iface = str(r.get("interface", "")).strip()
        rtype = str(r.get("type", "Port Group")).strip()
        ip_val = str(r.get("ip", "")).strip()
        purpose = str(r.get("purpose", "")).strip()
        role = str(r.get("role", "None")).strip()
        speed = str(r.get("speed", "")).strip()

        # If switch has real NICs, drop dummy "No Uplink" placeholders
        if iface == "No Uplink" and sw in switches_with_real_nics:
            continue

        # Drop ghost collapsed headers
        if re.search(r"PR\s*Spain\s*Fuenmayor.*VLab", iface):
            continue

        # 5-Tuple signature
        sig = (sw.lower(), iface.lower(), rtype.lower(), purpose.lower(), ip_val)
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)

        cleaned.append({
            "switch": sw,
            "interface": iface,
            "type": rtype,
            "ip": ip_val,
            "purpose": purpose,
            "role": role if rtype == "Uplink" else "None",
            "speed": speed,
        })

    # 3. Disambiguate VMkernel vs Port Group with identical purpose
    vmk_purposes = {
        (r["switch"].lower(), r["purpose"].lower())
        for r in cleaned if r["type"] == "VMkernel" and r["purpose"]
    }
    final_records = [
        r
        for r in cleaned
        if not (
            r["type"] == "Port Group"
            and (r["switch"].lower(), r["purpose"].lower()) in vmk_purposes
        )
    ]

    return final_records
