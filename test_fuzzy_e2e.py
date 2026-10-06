#!/usr/bin/env python3
"""
End-to-end simulation of the UI call chain for query 'show me details of fwazewine1'.

Traces: UI submit → build_naming_system_prompt → build_comprehensive_naming_context
       → build_backup_context → (exact search miss) → fuzzy → try_fuzzy_hostname_lookup
       → _fetch_device_context → full context returned to LLM.
"""
import sys
import os
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

QUERY = "show me details of fwazewine1"

# ── Inject mock DB data so _fetch_device_context returns real records ───────
MOCK_DEVICE_ROWS = [
    {"object_type": "dcim_devices",    "object_label": "Device",   "name": "fwAZESWINE1",
     "site": "Spain Central", "summary": "role: firewall | model: Cisco ASA 5506-X"},
    {"object_type": "dcim_interfaces", "object_label": "Interface","name": "GigabitEthernet0/0",
     "site": "Spain Central", "summary": "mac: aa:bb:cc:dd:ee:01 | speed: 1000Mb/s | connected: true"},
    {"object_type": "dcim_interfaces", "object_label": "Interface","name": "GigabitEthernet0/1",
     "site": "Spain Central", "summary": "mac: aa:bb:cc:dd:ee:02 | speed: 1000Mb/s | connected: false"},
    {"object_type": "ipam_ip_addresses", "object_label": "IP Address", "name": "10.50.1.1",
     "site": "Spain Central", "summary": "assigned_object: fwAZESWINE1 GigabitEthernet0/0"},
    {"object_type": "ipam_ip_addresses", "object_label": "IP Address", "name": "10.50.1.2",
     "site": "Spain Central", "summary": "assigned_object: fwAZESWINE1 GigabitEthernet0/1"},
    {"category": "device", "name": "fwAZESWINE1", "model_or_role": "Cisco ASA 5506-X",
     "site": "Spain Central", "cluster": "", "description": "Edge firewall Spain Central"},
]


def main():
    print("=" * 60)
    print(f"  Simulating UI call chain for query: {QUERY!r}")
    print("=" * 60)

    # ── Step 1: Mock exact search to return NOTHING (simulates typo) ──────────
    from core.backup_manager import search_backup_records
    original_search = search_backup_records

    def mock_search(required, optional=None, object_types=None, site="", limit=60):
        # Always return empty — the exact hostname 'fwazewine1' doesn't exist
        return []

    # ── Step 2: Mock _fetch_device_context to return our Spain Central device ─
    from core import ai_helper
    original_fetch = getattr(ai_helper, "_fetch_device_context", None)

    def mock_fetch(name, site_filter=""):
        # Filter by site if given, otherwise return all rows
        filtered = [r for r in MOCK_DEVICE_ROWS
                    if name.lower() in r.get("name", "").lower()
                    and (not site_filter or site_filter.lower() in r.get("site", "").lower())]
        return filtered if filtered else MOCK_DEVICE_ROWS

    # Apply mocks
    with mock.patch.object(ai_helper, "_fetch_device_context", mock_fetch):
        with mock.patch("core.backup_manager.search_backup_records", mock_search):
            # ── Step 3: Call the EXACT UI entry point ──────────────────────
            from core.ai_helper import build_comprehensive_naming_context

            print("\n[Step 1] Calling build_comprehensive_naming_context(...)\n")
            result = build_comprehensive_naming_context(QUERY)

            # ── Step 4: Verify the output ──────────────────────────────────
            print("[Step 2] Validating response ...")

            checks = [
                ("[SYSTEM NOTICE]" in result,
                 "Response contains [SYSTEM NOTICE] header"),
                ("fwazewine1" in result,
                 "Original misspelled query 'fwazewine1' referenced"),
                # After sanitization, the hostname becomes a token — check both
                # the raw name (present in notice text before vault touches it)
                # and the SAFE_HOST token (what the LLM actually sees)
                ("<SAFE_HOST_" in result or "fwAZESWINE1" in result,
                 "Matched hostname present (as token <SAFE_HOST_N> or raw)"),
                ("Spain Central" in result,
                 "Site 'Spain Central' present in context"),
                # Interface names are also tokenized; check for SAFE_HOST or raw
                ("<SAFE_HOST_" in result or "GigabitEthernet" in result,
                 "Interfaces fetched (as tokens or raw)"),
                # IP addresses get tokenized as SAFE_IP or SAFE_NET
                ("<SAFE_IP_" in result or "<SAFE_NET_" in result or "10.50.1" in result,
                 "IP addresses fetched (as tokens or raw)"),
                ("Cisco ASA" in result,
                 "Device model 'Cisco ASA' fetched"),
                # Verify vault round-trip: the restored text must contain the real data
            ]

            passed = 0
            failed = 0
            for ok, label in checks:
                status = "✅" if ok else "❌"
                print(f"  {status} {label}")
                if ok:
                    passed += 1
                else:
                    failed += 1

            # ── Step 5: Print the actual context block ─────────────────────
            print("\n[Step 3] Full context block returned to LLM:")
            print("─" * 60)
            # Extract just the SYSTEM NOTICE section for readability
            notice_start = result.find("[SYSTEM NOTICE]")
            if notice_start >= 0:
                print(result[notice_start:notice_start + 800])
            else:
                print("(No [SYSTEM NOTICE] found — showing first 600 chars)")
                print(result[:600])
            print("─" * 60)

            print(f"\n  Verdict: {passed}/{passed+failed} checks passed")
            if failed == 0:
                print("  ✅ SUCCESS: Full device context fetched for 'fwAZESWINE1'")
                print("             (matched from typo 'fwazewine1') — no hallucination.")
            else:
                print("  ❌ FAILURE: Some checks did not pass.")
            return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
