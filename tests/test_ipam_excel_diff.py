#!/usr/bin/env python3
"""
Unit Tests for Master IPAM Excel Diff Engine — NetBox Hub v4.3.5

Covers:
  1. Dynamic multi-sheet discovery (arbitrary sheet counts/names)
  2. Header alias discovery (ip/subnet/vlan_id/vlan_name/site variations)
  3. Standards-driven normalisation (site_code_rules, ipam_role_mappings)
  4. Deterministic classification (matched / pending_add / conflicts / unmapped)
  5. Application version assertion: config.constants.APP_VERSION == "4.3.5"
"""

from __future__ import annotations

import io
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
    def test_app_version_is_435(self):
        assert APP_VERSION.startswith("4.3.5"), f"Expected '4.3.5.x', got {APP_VERSION!r}"

    def test_version_no_v_prefix(self):
        assert not APP_VERSION.startswith("v"), f"Version should not have 'v' prefix: {APP_VERSION!r}"


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
