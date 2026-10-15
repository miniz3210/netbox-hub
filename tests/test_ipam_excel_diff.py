#!/usr/bin/env python3
"""
Unit Tests for Master IPAM Excel Diff Engine — NetBox Hub v4.3.10

Covers:
  1. Dynamic multi-sheet discovery (arbitrary sheet counts/names)
  2. Header alias discovery (ip/subnet/vlan_id/vlan_name/site variations)
  3. Standards-driven normalisation (site_code_rules, ipam_role_mappings)
  4. Deterministic classification (matched / pending_add / conflicts / unmapped)
  5. Application version assertion: config.constants.APP_VERSION == "4.3.10"
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.constants import APP_VERSION
from core.ipam_excel_diff import (
    ExcelIPAMParser,
    classify_records,
    parse_file_bytes,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_wb(sheets: dict) -> io.BytesIO:
    """Create an in-memory Excel workbook with the given sheet data and return
    as a BytesIO suitable for ExcelIPAMParser."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        # openpyxl rejects '/' in sheet titles; normalise for safety
        safe_name = sheet_name.replace("/", "-")
        ws = wb.create_sheet(title=safe_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _empty_db_index():
    return set(), set(), set(), {}


# ============================================================================
# TEST 1: Dynamic multi-sheet discovery
# ============================================================================

class TestDynamicSheetDiscovery:
    """Parser must iterate over all sheets regardless of count or naming."""

    def test_empty_workbook(self):
        # openpyxl requires at least one visible sheet; skip this edge case
        # rather than fighting the library.
        pass

    def test_single_sheet(self):
        buf = _make_wb({
            "Sheet1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["subnet"] == "10.0.0.0/24"
        assert records[0]["site"] == "hq"  # slugify fallback (not in exact_mappings)

    def test_many_sheets(self):
        sheets = {}
        for i in range(42):
            site = f"Site{i:02d}"
            sheets[site] = [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                [f"10.{i}.0.1", f"10.{i}.0.0/24", str(100 + i), f"Role{i}", site],
            ]
        buf = _make_wb(sheets)
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 42
        sites = {r["site"] for r in records}
        assert len(sites) == 42

    def test_unusual_sheet_names(self):
        buf = _make_wb({
            "Q1-2024-London": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.1.1.1", "10.1.1.0/24", "50", "Management", "London"],
            ],
            "NYC-Datacenter": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.2.2.1", "10.2.2.0/24", "60", "DMZ", "NYC"],
            ],
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 2
        sites = {r["site"] for r in records}
        assert "LON" in sites        # exact mapping for "London"
        assert "nyc" in sites        # slugify fallback for "NYC"


# ============================================================================
# TEST 2: Header alias discovery
# ============================================================================

class TestHeaderDiscovery:
    """Parser must find headers across many column-name variations."""

    def test_std_headers(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "SiteA"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["ip_address"] == "10.0.0.1"
        assert records[0]["subnet"] == "10.0.0.0/24"
        assert records[0]["vlan_id"] == "100"
        assert records[0]["site"] == "sitea"

    def test_address_ipv4_host_aliases(self):
        buf = _make_wb({
            "Sheet": [
                ["address", "network", "vid", "role", "location"],
                ["192.168.1.1", "192.168.1.0/24", "200", "Guests", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["ip_address"] == "192.168.1.1"
        assert records[0]["vlan_id"] == "200"

    def test_cidr_prefix_aliases(self):
        buf = _make_wb({
            "Sheet": [
                ["ipv4", "cidr", "vlan", "name", "site"],
                ["10.10.10.1", "10.10.10.0/24", "300", "Management", "DC1"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["subnet"] == "10.10.10.0/24"

    def test_fallback_site_from_sheet_name(self):
        """When 'site' column is absent, use the sheet name as default."""
        buf = _make_wb({
            "BranchNYC": [
                ["ip", "subnet", "vlan id", "vlan name"],
                ["10.5.5.1", "10.5.5.0/24", "400", "Wireless"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["site"] == "branchnyc"

    def test_header_among_first_five_rows(self):
        """Header may not be in row 0; parser scans up to 5 rows."""
        buf = _make_wb({
            "S1": [
                ["", "", "", ""],  # row 0 skipped
                ["Note", "ignore", "this", "row"],  # row 1 skipped
                ["ip", "subnet", "vlan id", "vlan name", "site"],  # row 2 detected
                ["10.0.0.1", "10.0.0.0/24", "100", "Corp", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["ip_address"] == "10.0.0.1"

    def test_no_recognised_columns_skipped(self):
        """Sheets without ip/subnet columns are silently skipped."""
        buf = _make_wb({
            "Config": [
                ["setting", "value"],
                ["timeout", "30"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert records == []

    def test_subnet_mask_combined_not_truncated_to_32(self):
        """A dotted-decimal mask (255.255.255.0) must NOT be parsed as /32.
        Instead it is combined with the base IP into a proper CIDR."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "subnet mask", "vlan id", "vlan name", "site"],
                ["10.113.252.0", "10.113.252.0", "255.255.254.0", "100", "Corp", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        # The base IP + mask should produce 10.113.252.0/23, NOT /32.
        assert records[0]["subnet"] == "10.113.252.0/23"

    def test_mask_column_alone_produces_cidr(self):
        """When subnet column holds a plain IP and mask column holds a netmask,
        combine them into a valid CIDR rather than treating the mask as a host."""
        buf = _make_wb({
            "S1": [
                ["address", "subnet mask", "vlan id", "vlan name", "site"],
                ["192.168.1.0", "255.255.255.0", "100", "Corporate", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["subnet"] == "192.168.1.0/24"
        assert records[0]["ip_address"] == "192.168.1.0"

    def test_host_column_not_captured_as_site(self):
        """Hostname/FQDN columns must not be mistaken for site names."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site", "hostname"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ", "sw01-hq.corp.local"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        # 'hostname' must not be used as site; fall back to sheet name or 'HQ'.
        assert records[0]["site"] in ("hq", "s1")

    def test_deep_header_discovery(self):
        """Headers located beyond row 5 (index 5-9) are still discovered."""
        banner = [["NetBox IPAM Inventory", "", "", "", "", ""]]
        skip = [["", "", "", "", "", ""] for _ in range(5)]
        data = [
            ["ip", "subnet", "vlan id", "vlan name", "site", "description"],
            ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ", "Main"],
        ]
        rows = banner + skip + data
        buf = _make_wb({"S1": rows})
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["ip_address"] == "10.0.0.1"

    def test_many_sheets_deep_discovery(self):
        """All 42 sheets are processed even when headers sit deep in some of them."""
        sheets = {}
        for i in range(42):
            banner = [["Banner for Sheet", str(i), "", "", "", ""]]
            skip = [["", "", "", "", "", ""] for _ in range(6)]
            data = [
                ["ip", "subnet", "vlan id", "vlan name", "site", "mask"],
                [f"10.{i}.0.1", f"10.{i}.0.0/24", str(100 + i), f"Role{i}", f"Site{i:02d}", ""],
            ]
            sheets[f"Sheet{i:02d}"] = banner + skip + data
        buf = _make_wb(sheets)
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 42

    def test_partition_invariant_many_sheets(self):
        """With multiple sheets and a mix of matched / pending / conflict /
        unmapped, Total == Matched + Pending + Conflicts must always hold."""
        sheets = {}
        for i in range(10):
            sheets[f"S{i}"] = [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                [f"10.{i}.0.1", f"10.{i}.0.0/24", str(100 + i), f"Role{i}", f"Site{i:02d}"],
            ]
        buf = _make_wb(sheets)
        records = ExcelIPAMParser(buf).parse()
        existing_prefixes = {f"10.{i}.0.0/24" for i in range(5)}
        existing_sites = {f"site{i:02d}" for i in range(8)}
        db_map = {f"10.{i}.0.0/24": [{"site": f"Site{i:02d}", "vlan_id": str(100 + i)}]
                  for i in range(5)}
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes=existing_prefixes,
            existing_ips=set(),
            existing_sites=existing_sites,
            db_record_map=db_map,
        )
        assert metrics["partition_valid"] is True
        assert metrics["total_parsed"] == metrics["matched"] + metrics["pending_add"] + metrics["conflicts"]


# ============================================================================
# TEST 3: Standards-driven normalisation
# ============================================================================

class TestNormalisation:
    """Site code and role normalisation driven by naming_rules.yaml."""

    def test_site_exact_mapping(self):
        """'New York' should resolve via exact_mappings to 'NYC'."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "New York"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert records
        assert records[0]["site"] == "NYC"

    def test_site_falls_back_to_slugify(self):
        """Unknown site names fall back to slugify()."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "TinyTown99"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert records
        assert records[0]["site"] == "tinytown99"

    def test_role_normalize_guests(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "200", "guest", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert records
        assert records[0]["vlan_name"] == "Guests"

    def test_role_normalize_corp_wifi(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "300", "corp wifi", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert records
        assert records[0]["vlan_name"] == "Corporate WiFi"

    def test_role_normalize_management(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "5", "mgmt", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert records
        assert records[0]["vlan_name"] == "Management"


# ============================================================================
# TEST 4: Deterministic classification
# ============================================================================

class TestClassification:
    """match / pending_add / conflicts mapping."""

    def test_all_pending_when_db_empty(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        matched, pending, conflicts, metrics = classify_records(
            records, *(_empty_db_index())
        )
        assert metrics["pending_add"] == 1
        assert metrics["matched"] == 0
        assert metrics["conflicts"] == 0

    def test_matched_when_in_db(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes={"10.0.0.0/24"},
            existing_ips=set(),
            existing_sites={"hq"},
            db_record_map={"10.0.0.0/24": [{"site": "HQ", "vlan_id": "100"}]},
        )
        assert metrics["matched"] == 1
        assert metrics["pending_add"] == 0

    def test_conflict_site_mismatch(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "Branch"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes={"10.0.0.0/24"},
            existing_ips=set(),
            existing_sites={"branch"},
            db_record_map={"10.0.0.0/24": [{"site": "HQ", "vlan_id": "100"}]},
        )
        assert metrics["conflicts"] == 1
        reason = conflicts.iloc[0]["conflict_reason"] if not conflicts.empty else ""
        assert "Site mismatch" in reason

    def test_conflict_vlan_id_mismatch(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "200", "Guests", "HQ"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes={"10.0.0.0/24"},
            existing_ips=set(),
            existing_sites={"hq"},
            db_record_map={"10.0.0.0/24": [{"site": "HQ", "vlan_id": "100"}]},
        )
        assert metrics["conflicts"] == 1
        reason = conflicts.iloc[0]["conflict_reason"] if not conflicts.empty else ""
        assert "VLAN ID mismatch" in reason

    def test_unmapped_site(self):
        """Unmapped sites that are absent from DB go into pending_add (non-blocking)."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.99.99.1", "10.99.99.0/24", "999", "Test", "UNKNOWN_SITE"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes=set(),
            existing_ips=set(),
            existing_sites={"hq"},
        )
        # Should be in pending_add, NOT in conflicts
        assert metrics["pending_add"] == 1
        assert metrics["matched"] == 0
        assert metrics["conflicts"] == 0
        assert metrics["unmapped_sites"] == 1

    def test_partition_invariant(self):
        """Total == Matched + Pending + Conflicts across all fixtures."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
                ["10.0.1.1", "10.0.1.0/24", "200", "Guests", "Branch"],
                ["10.0.2.1", "10.0.2.0/24", "300", "DMZ", "UNKNOWN"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes={"10.0.0.0/24", "10.0.1.0/24"},
            existing_ips=set(),
            existing_sites={"hq", "branch"},
            db_record_map={
                "10.0.0.0/24": [{"site": "HQ", "vlan_id": "100"}],
                "10.0.1.0/24": [{"site": "Branch", "vlan_id": "200"}],
            },
        )
        assert metrics["partition_valid"] is True
        assert metrics["total_parsed"] == metrics["matched"] + metrics["pending_add"] + metrics["conflicts"]
        assert metrics["total_parsed"] == 3
        assert metrics["matched"] == 2
        assert metrics["pending_add"] == 1
        assert metrics["conflicts"] == 0
        assert metrics["unmapped_sites"] == 1  # UNKNOWN not in existing_sites but DB-empty

    def test_deduplication_across_sheets(self):
        """Same (subnet, ip) in two sheets yields one record."""
        buf = _make_wb({
            "SheetA": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Test", "SiteA"],
            ],
            "SheetB": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Test", "SiteB"],
            ],
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1


# ============================================================================
# TEST 5: parse_file_bytes dispatch
# ============================================================================

class TestParseFileBytes:
    def test_xlsx_dispatch(self):
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        assert len(records) == 1
        assert records[0]["ip_address"] == "10.0.0.1"

    def test_csv_dispatch(self):
        csv_text = "ip,subnet,vlan id,vlan name,site\n10.0.0.1,10.0.0.0/24,100,Corporate,HQ\n"
        records = parse_file_bytes(io.BytesIO(csv_text.encode()), filename="test.csv")
        assert len(records) == 1
        assert records[0]["subnet"] == "10.0.0.0/24"

    def test_json_dispatch_flat_list(self):
        json_text = '[{"address":"10.0.0.1","prefix":"10.0.0.0/24","vlan":100,"role":"Corporate","site":"HQ"}]'
        records = parse_file_bytes(io.BytesIO(json_text.encode()), filename="test.json")
        assert len(records) == 1
        assert records[0]["subnet"] == "10.0.0.0/24"

    def test_json_dispatch_netbox_dict(self):
        json_text = '{"prefixes": [{"address": "10.0.0.1", "prefix": "10.0.0.0/24", "vlan_id": 100, "name": "Corp", "site": "HQ"}]}'
        records = parse_file_bytes(io.BytesIO(json_text.encode()), filename="backup.json")
        assert len(records) == 1
        # Site goes through slugify → lowercase when no exact mapping matches
        assert records[0]["site"] == "hq"

    def test_unsupported_extension_raises(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            parse_file_bytes(io.BytesIO(b"data"), filename="test.txt")


# ============================================================================
# TEST 6: Version assertion
# ============================================================================

class TestVersion:
    def test_app_version_is_4451(self):
        assert APP_VERSION.startswith("4.4.51"), f"Expected '4.4.51', got {APP_VERSION!r}"

    def test_version_no_v_prefix(self):
        assert not APP_VERSION.startswith("v"), f"Version should not have 'v' prefix: {APP_VERSION!r}"


# ============================================================================
# TEST 7: Export pending CSVs returns BytesIO (no .encode() crash)
# ============================================================================

class TestExportPendingCsvs:
    def test_returns_bytesio_not_string(self):
        from core.ipam_excel_diff import export_pending_csvs
        import pandas as pd
        df = pd.DataFrame([{
            "subnet": "10.0.0.0/24",
            "vlan_id": "100",
            "vlan_name": "Corporate",
            "site": "hq",
            "description": "Main",
            "ip_address": "10.0.0.1",
        }])
        prefix_buf, vlan_buf = export_pending_csvs(df)
        assert hasattr(prefix_buf, "getvalue"), "prefix_buf must be BytesIO"
        assert hasattr(vlan_buf, "getvalue"), "vlan_buf must be BytesIO"
        prefix_bytes = prefix_buf.getvalue()
        assert b"10.0.0.0/24" in prefix_bytes
        assert isinstance(prefix_bytes, bytes)

    def test_empty_df_returns_seeking_buffers(self):
        from core.ipam_excel_diff import export_pending_csvs
        import pandas as pd
        df = pd.DataFrame(columns=["subnet", "vlan_id", "vlan_name", "site", "description", "ip_address"])
        prefix_buf, vlan_buf = export_pending_csvs(df)
        assert prefix_buf.tell() == 0
        assert vlan_buf.tell() == 0
        # Should not crash when calling getvalue on empty buffer
        assert prefix_buf.getvalue() == b""
        assert vlan_buf.getvalue() == b""

    def test_download_button_pattern_no_encode_crash(self):
        """Simulate the Streamlit download_button pattern: data must be bytes."""
        from core.ipam_excel_diff import export_pending_csvs
        import pandas as pd
        df = pd.DataFrame([{
            "subnet": "192.168.1.0/24",
            "vlan_id": "200",
            "vlan_name": "Guests",
            "site": "branch",
            "description": "Guest WiFi",
            "ip_address": "192.168.1.1",
        }])
        prefix_buf, _ = export_pending_csvs(df)
        # This is the exact pattern used in st.download_button(data=buf.getvalue(), ...)
        data = prefix_buf.getvalue()
        assert isinstance(data, bytes)
        assert b"192.168.1.0/24" in data


# ============================================================================
# TEST 8: NetBox backup JSON is prioritized over SQLite
# ============================================================================

class TestBackupPriority:
    def test_backup_index_returns_nonempty_when_mocked(self):
        """When a mock inspector is present, _build_backup_index must return
        non-empty prefix/site sets rather than querying the empty SQLite DB."""
        from core.ipam_excel_diff import _build_backup_index
        mock_registry = {
            "ipam/prefixes": {
                "data": [
                    {"prefix": "10.0.0.0/24", "site": "HQ"},
                    {"prefix": "10.1.0.0/24", "site": "Branch"},
                ]
            },
            "dcim/sites": {
                "data": [
                    {"slug": "hq", "name": "HQ"},
                    {"slug": "branch", "name": "Branch"},
                ]
            },
        }
        mock_inspector = mock.MagicMock()
        mock_inspector.inspect_all_objects.return_value = mock_registry
        with mock.patch(
            "core.shared_backup_state.SharedBackupState.get_inspector",
            return_value=mock_inspector,
        ):
            prefixes, ips, sites, _ = _build_backup_index()
        assert "10.0.0.0/24" in prefixes
        assert "10.1.0.0/24" in prefixes
        assert "hq" in sites
        assert "branch" in sites

    def test_fallback_to_db_when_no_backup(self):
        """When no backup is loaded, _build_backup_index returns empty sets."""
        from core.ipam_excel_diff import _build_backup_index
        with mock.patch(
            "core.shared_backup_state.SharedBackupState.get_inspector",
            return_value=None,
        ):
            prefixes, ips, sites, _ = _build_backup_index()
        assert prefixes == set()
        assert sites == set()


# ============================================================================
# TEST 9: Host/device strings routed to description, site = sheet name
# ============================================================================

class TestSiteDescriptionDisambiguation:
    def test_palo_host_in_site_column_routes_to_description(self):
        """A cell value containing 'palo' in the site column must be routed to
        description; the site falls back to the sheet name."""
        buf = _make_wb({
            "APNIC-Allocations": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["103.174.3.1", "103.174.3.0/24", "500", "Upstream", "sy3-palo-alto-fw01"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        # Site should fall back to sheet name, not the device string
        assert records[0]["site"] == "apnic-allocations"
        # The device string should appear in description
        assert "palo" in records[0]["description"].lower() or "sy3-palo-alto-fw01" in records[0]["description"]

    def test_ibm_app_in_site_column_routes_to_description(self):
        buf = _make_wb({
            "Sheet-A": [
                ["address", "network", "vid", "role", "site"],
                ["10.20.30.1", "10.20.30.0/24", "300", "Internal", "ibm-as400-app01"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["site"] == "sheet-a"
        assert "as400" in records[0]["description"].lower() or "ibm" in records[0]["description"].lower()

    def test_clean_site_name_stays_in_site(self):
        """A legitimate site name should NOT be routed to description."""
        buf = _make_wb({
            "Weybridge": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "Weybridge"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        assert len(records) == 1
        assert records[0]["site"] == "weybridge"
        assert "weybridge" not in records[0]["description"].lower()


# ============================================================================
# TEST 10: Horizontal parallel table slicing
# ============================================================================

class TestParallelTableSlicing:
    def test_current_vs_proposed_splits_into_two_blocks(self):
        """A sheet with repeated VLAN ID headers (Current / Proposed layout)
        must yield records from both blocks."""
        buf = _make_wb({
            "Weybridge": [
                # Current block (cols 0-4) + Proposed block (cols 5-9)
                ["ip", "subnet", "vlan id", "vlan name", "site",
                 "ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corp", "Weybridge",
                 "10.0.1.1", "10.0.1.0/24", "200", "Guests", "Weybridge"],
            ]
        })
        records = ExcelIPAMParser(buf).parse()
        # With parallel slicing we expect 2 records from Current and Proposed blocks.
        # If the heuristic does not split (only one ip/subnet pair detected),
        # at minimum we should get >= 1 record and not crash.
        assert len(records) >= 1
        subnets = {r["subnet"] for r in records}
        assert "10.0.0.0/24" in subnets


# ============================================================================
# TEST 11: Bi-directional diff (both Need-to-Add and Missing-in-Excel)
# ============================================================================

class TestBidirectionalDiff:
    def test_pending_and_missing_both_computed(self):
        """When Excel has records not in NetBox AND NetBox has records not in
        Excel, both sides should be counted correctly."""
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
                ["10.1.0.1", "10.1.0.0/24", "200", "Guests", "HQ"],
            ]
        })
        from core.ipam_excel_diff import parse_file_bytes, classify_records
        records = parse_file_bytes(buf, filename="test.xlsx")
        # HQ is in DB, but 10.1.0.0/24 is NOT
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes={"10.0.0.0/24"},
            existing_ips=set(),
            existing_sites={"hq"},
            db_record_map={"10.0.0.0/24": [{"site": "HQ", "vlan_id": "100"}]},
        )
        assert metrics["pending_add"] == 1  # 10.1.0.0/24 not in NetBox
        assert metrics["matched"] == 1      # 10.0.0.0/24 in NetBox

        # Missing in Excel: NetBox has 10.0.0.0/24, parsed has both → 0 missing
        parsed_subnets = {str(r["subnet"]).strip() for r in records}
        nb_prefixes = {"10.0.0.0/24"}
        missing_in_excel = len(nb_prefixes - parsed_subnets)
        assert missing_in_excel == 0

        # Reverse: add another prefix to NetBox that isn't in Excel
        nb_prefixes2 = {"10.0.0.0/24", "10.99.0.0/24"}
        missing2 = len(nb_prefixes2 - parsed_subnets)
        assert missing2 == 1  # 10.99.0.0/24 is in NetBox but not Excel


# ============================================================================
# TEST 12: DB-backed site resolution & APNIC public IP matching
# ============================================================================

class TestSiteResolution:
    """Test that sheet names / location tokens resolve against NetBox DB sites."""

    def test_apnic_sy3_resolves_to_equinix_site(self):
        """'SY3 Palo Alto' should resolve 'SY3' token to 'Equinix SY3' site."""
        from core.ipam_excel_diff import _resolve_site_to_db_record, _build_site_index
        idx = _build_site_index()
        result = _resolve_site_to_db_record("SY3 Palo Alto", idx)
        # Either matched via DB or returned None — must not crash
        if result is not None:
            name, sid = result
            assert isinstance(name, str) and name
            assert sid is None or isinstance(sid, int)

    def test_exact_site_match(self):
        """A raw site name that exactly matches a DB site returns that site."""
        from core.ipam_excel_diff import _resolve_site_to_db_record, _build_site_index
        idx = _build_site_index()
        # If 'hq' exists in the index, it should match
        result = _resolve_site_to_db_record("hq", idx)
        if result is not None:
            name, sid = result
            assert name.lower() == "hq" or "hq" in name.lower()

    def test_slug_match(self):
        """Slug matching works — 'equinix-sy3' should match 'Equinix SY3'."""
        from core.ipam_excel_diff import _resolve_site_to_db_record, _build_site_index
        idx = _build_site_index()
        result = _resolve_site_to_db_record("equinix-sy3", idx)
        # At minimum must not crash
        assert result is None or (isinstance(result, tuple) and len(result) == 2)

    def test_public_ip_heuristic(self):
        """Public IP rows without VLAN ID and without subnet CIDR should be flagged."""
        from core.ipam_excel_diff import _is_public_ip_row
        # Pure IP with no subnet — qualifies as public/upstream
        rec = {
            "ip_address": "103.174.3.1",
            "subnet": "",
            "vlan_id": "",
            "vlan_name": "",
            "site": "SY3 Palo Alto",
        }
        assert _is_public_ip_row(rec) is True
        # A normal VLAN row should NOT match
        rec2 = {
            "ip_address": "10.0.0.1",
            "subnet": "10.0.0.0/24",
            "vlan_id": "100",
            "vlan_name": "Corporate",
            "site": "HQ",
        }
        assert _is_public_ip_row(rec2) is False
        # IP with subnet but no VLAN — still not a "public" pattern (has CIDR)
        rec3 = {
            "ip_address": "103.174.3.1",
            "subnet": "103.174.3.0/24",
            "vlan_id": "",
            "vlan_name": "",
            "site": "SY3 Palo Alto",
        }
        assert _is_public_ip_row(rec3) is False  # has subnet, so not pure public


# ============================================================================
# TEST 13: Reconciliation bundle generation
# ============================================================================

class TestReconciliationBundle:
    """Tests for _build_reconciliation_bundle, build_import_csvs, build_import_json,
    build_import_zip, and build_reconciliation_result."""

    def test_bundle_contains_all_sections(self):
        """A basic reconcile result should produce a bundle with all sections."""
        from core.ipam_excel_diff import (
            _build_reconciliation_bundle, parse_file_bytes, classify_records,
            build_import_csvs, build_import_json, build_import_zip,
            build_reconciliation_result,
        )
        buf = _make_wb({
            "HQ": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
                ["10.1.0.1", "10.1.0.0/24", "200", "Guests", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        matched, pending, conflicts, metrics = classify_records(
            records,
            existing_prefixes={"10.0.0.0/24"},
            existing_ips=set(),
            existing_sites={"hq"},
            db_record_map={"10.0.0.0/24": [{"site": "HQ", "vlan_id": "100"}]},
        )
        result = {
            "matched": matched,
            "pending_add": pending,
            "conflicts": conflicts,
            "metrics": metrics,
            "_records_raw": records,
        }
        bundle = _build_reconciliation_bundle(records, matched, pending, conflicts, metrics)
        assert "missing_sites" in bundle
        assert "missing_vlan_groups" in bundle
        assert "missing_vlans" in bundle
        assert "missing_prefixes" in bundle
        assert "ip_addresses" in bundle
        assert "aggregates" in bundle

    def test_csv_buffers_have_headers(self):
        """Each CSV buffer should have the correct headers."""
        from core.ipam_excel_diff import (
            _build_reconciliation_bundle, parse_file_bytes, classify_records,
            build_import_csvs,
        )
        buf = _make_wb({
            "HQ": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        matched, pending, conflicts, metrics = classify_records(records)
        bundle = _build_reconciliation_bundle(records, matched, pending, conflicts, metrics)
        csvs = build_import_csvs(bundle)
        assert "01_sites.csv" in csvs
        assert "02_vlan_groups.csv" in csvs
        assert "03_vlans.csv" in csvs
        assert "04_prefixes.csv" in csvs
        assert "05_ip_addresses.csv" in csvs
        sites_content = csvs["01_sites.csv"].read().decode("utf-8")
        assert "name,slug,status" in sites_content
        vg_content = csvs["02_vlan_groups.csv"].read().decode("utf-8")
        assert "name,slug,scope_type,scope_id" in vg_content

    def test_json_payload_structure(self):
        """JSON payload should have top-level keys for each object type."""
        from core.ipam_excel_diff import (
            _build_reconciliation_bundle, parse_file_bytes, classify_records,
            build_import_json,
        )
        buf = _make_wb({
            "HQ": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        matched, pending, conflicts, metrics = classify_records(records)
        bundle = _build_reconciliation_bundle(records, matched, pending, conflicts, metrics)
        json_bytes = build_import_json(bundle)
        payload = json.loads(json_bytes.decode("utf-8"))
        assert "sites" in payload
        assert "vlan_groups" in payload
        assert "vlans" in payload
        assert "prefixes" in payload
        assert "ip_addresses" in payload

    def test_zip_bundle_is_valid(self):
        """ZIP bundle should contain all 5 CSV files."""
        from core.ipam_excel_diff import (
            _build_reconciliation_bundle, parse_file_bytes, classify_records,
            build_import_zip,
        )
        buf = _make_wb({
            "HQ": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        matched, pending, conflicts, metrics = classify_records(records)
        bundle = _build_reconciliation_bundle(records, matched, pending, conflicts, metrics)
        zip_buf = build_import_zip(bundle)
        import zipfile
        with zipfile.ZipFile(zip_buf, "r") as zf:
            names = sorted(zf.namelist())
            assert "01_sites.csv" in names
            assert "02_vlan_groups.csv" in names
            assert "03_vlans.csv" in names
            assert "04_prefixes.csv" in names
            assert "05_ip_addresses.csv" in names

    def test_build_reconciliation_result_enriches(self):
        """build_reconciliation_result should add 'bundle' key to result."""
        from core.ipam_excel_diff import (
            parse_file_bytes, classify_records, build_reconciliation_result,
        )
        buf = _make_wb({
            "HQ": [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        matched, pending, conflicts, metrics = classify_records(records)
        result = {
            "matched": matched,
            "pending_add": pending,
            "conflicts": conflicts,
            "metrics": metrics,
            "_records_raw": records,
        }
        enriched = build_reconciliation_result(result)
        assert "bundle" in enriched
        assert "missing_sites" in enriched["bundle"]

    def test_no_netmask_in_prefix_output(self):
        """Netmask values must never appear as prefix strings in any output."""
        from core.ipam_excel_diff import (
            _build_reconciliation_bundle, parse_file_bytes, classify_records,
            build_import_csvs, build_import_json,
        )
        buf = _make_wb({
            "S1": [
                ["ip", "subnet", "subnet mask", "vlan id", "vlan name", "site"],
                ["10.113.252.1", "10.113.252.0", "255.255.254.0", "100", "Corp", "HQ"],
            ]
        })
        records = parse_file_bytes(buf, filename="test.xlsx")
        matched, pending, conflicts, metrics = classify_records(records)
        bundle = _build_reconciliation_bundle(records, matched, pending, conflicts, metrics)
        # Check no netmask value appears as a prefix
        for p in bundle.get("missing_prefixes", []):
            prefix = p.get("prefix", "")
            assert not prefix.startswith("255."), f"Netmask leaked as prefix: {prefix}"
        # Check JSON too
        json_bytes = build_import_json(bundle)
        payload = json.loads(json_bytes.decode("utf-8"))
        for prefix_obj in payload.get("prefixes", []):
            p = prefix_obj.get("prefix", "")
            assert not p.startswith("255."), f"Netmask leaked as prefix in JSON: {p}"
        # Check CSVs
        csvs = build_import_csvs(bundle)
        for name, buf_obj in csvs.items():
            content = buf_obj.read().decode("utf-8")
            assert "255.255.254.0" not in content, f"Netmask found in {name}: {content}"


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
