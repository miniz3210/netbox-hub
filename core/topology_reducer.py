"""
Universal Topology Reducer (v4.7.3)
100% Platform-Agnostic: Merges multi-screenshot records, enforces expanded-state
precedence (expanded cards purge collapsed placeholders), and deduplicates 5-tuples.
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

    # 1. Clean switch names using YAML normalizations
    for r in raw_records:
        raw_sw = str(r.get("switch", "")).strip()
        r["switch"] = _apply_token_normalizations(raw_sw, normalizations)

    # 2. Expanded State Precedence:
    # Identify switches that have REAL content in subsequent frames (either physical NICs or real port groups)
    switches_with_real_content: Set[str] = set()
    for r in raw_records:
        sw = str(r.get("switch", "")).strip()
        iface = str(r.get("interface", "")).strip()
        purpose = str(r.get("purpose", "")).strip()
        rtype = str(r.get("type", "")).strip()

        if rtype == "Uplink" and iface and iface != "No Uplink":
            switches_with_real_content.add(sw)
        elif rtype != "Uplink" and purpose:
            switches_with_real_content.add(sw)

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

        # If switch has real content in an expanded frame, drop empty collapsed placeholder records
        if iface == "No Uplink" and purpose == "" and sw in switches_with_real_content:
            continue

        # Drop any leaked switch headers or collapsed glyphs
        if re.search(r"(?:Standard|Distributed)\s*Switch:", purpose, re.I) or purpose.startswith(">"):
            continue
        if re.search(r"(?:Standard|Distributed)\s*Switch:", iface, re.I):
            continue

        # Drop dynamic noise
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
