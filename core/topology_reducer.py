"""
Universal Topology Reducer (v4.7.2)
100% Platform-Agnostic: Applies dynamic precedence, normalizations, and deduplication.
"""

import re
from typing import List, Dict, Any, Set, Tuple


def _apply_token_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    for rule in normalizations:
        pat = rule.get("pattern")
        rep = rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


def reduce_topology_pipeline(
    raw_records: List[Dict[str, Any]], 
    plat_config: Dict[str, Any],
    platform_tokens: Dict[str, Any]
) -> List[Dict[str, Any]]:
    if not raw_records:
        return []

    normalizations = plat_config.get("token_normalizations", [])
    noise_filters = plat_config.get("noise_filters", [])
    if noise_filters:
        escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
        noise_re = re.compile(r"^(?:" + r"|".join(escaped) + r")$|(?:" + r"|".join(escaped) + r")", re.I)
    else:
        noise_re = None

    # 1. Canonicalize Switch Names across all records using YAML normalizations
    for r in raw_records:
        raw_sw = str(r.get("switch", "")).strip()
        r["switch"] = _apply_token_normalizations(raw_sw, normalizations)

    # 2. Expanded State Precedence:
    # Identify switches that possess real physical NICs
    switches_with_real_nics: Set[str] = set()
    for r in raw_records:
        sw = str(r.get("switch", "")).strip()
        iface = str(r.get("interface", "")).strip()
        rtype = str(r.get("type", "")).strip()
        if rtype == "Uplink" and iface and iface != "No Uplink":
            switches_with_real_nics.add(sw)

    # 3. Filter & Deduplicate
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

        # Drop noise leaks using dynamic YAML noise_filters
        if noise_re and (noise_re.search(purpose) or noise_re.search(iface)):
            continue

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
            "speed": speed
        })

    # 4. Disambiguate VMkernel vs Port Group with identical purpose
    vmk_purposes = {
        (r["switch"].lower(), r["purpose"].lower()) 
        for r in cleaned if r["type"] == "VMkernel" and r["purpose"]
    }
    final_records = [
        r for r in cleaned 
        if not (r["type"] == "Port Group" and (r["switch"].lower(), r["purpose"].lower()) in vmk_purposes)
    ]

    return final_records
