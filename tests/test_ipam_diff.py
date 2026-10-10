#!/usr/bin/env python3
"""
Unit Tests for Offline IPAM Diff Engine (v4.3)

Covers:
  1. IP and Subnet normalization (valid pass, invalid rejected)
  2. Dynamic header detection across column-name variations
  3. Conflict vs. matched vs. pending-add classification
  4. Version assertion: config.constants.APP_VERSION == "4.3"
"""

import csv
import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.diff_ipam_sqlite import (
    ExcelIPAMParser,
    LocalSQLiteManager,
    classify_records,
    write_csv,
)
from config.constants import APP_VERSION


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_explorer(tmpdir: str, sheet_data: dict) -> str:
    """Build a minimal Excel workbook with named sheets and return its path."""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet
    for sheet_name, rows in sheet_data.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    path = os.path.join(tmpdir, "test_inventory.xlsx")
    wb.save(path)
    return path


def _make_db(tmpdir: str) -> str:
    """Create a temporary SQLite DB with ipam_inventory populated."""
    import sqlite3
    db_path = os.path.join(tmpdir, "test_vault.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE ipam_inventory (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address  TEXT UNIQUE,
            subnet      TEXT,
            vlan_id     TEXT,
            vlan_name   TEXT,
            site        TEXT,
            description TEXT,
            status      TEXT DEFAULT 'Active'
        )
    """)
    conn.commit()
    return db_path


# ============================================================================
# TEST 1: IP and Subnet Normalisation
# ============================================================================
class TestIPSubnetNormalization:
    """Valid IPs/CIDRs pass; invalid strings are rejected."""

    def test_valid_ipv4(self):
        assert ExcelIPAMParser._normalise_ip("10.0.0.1") == "10.0.0.1"

    def test_valid_ipv4_leading_zeros(self):
        # Leading zeros are rejected by ipaddress module
        assert ExcelIPAMParser._normalise_ip("010.000.000.001") == ""

    def test_invalid_ip_empty(self):
        assert ExcelIPAMParser._normalise_ip("") == ""

    def test_invalid_ip_string(self):
        assert ExcelIPAMParser._normalise_ip("not-an-ip") == ""

    def test_invalid_ip_nan(self):
        assert ExcelIPAMParser._normalise_ip("nan") == ""

    def test_invalid_ip_none(self):
        assert ExcelIPAMParser._normalise_ip("None") == ""

    def test_invalid_ip_out_of_range(self):
        assert ExcelIPAMParser._normalise_ip("256.1.1.1") == ""

    def test_valid_cidr(self):
        assert ExcelIPAMParser._normalise_subnet("10.0.0.0/24") == "10.0.0.0/24"

    def test_valid_cidr_no_netmask(self):
        assert ExcelIPAMParser._normalise_subnet("172.16.0.0/16") == "172.16.0.0/16"

    def test_dot_notation_converted(self):
        # "10.113.64.0.24" → "10.113.64.0/24" (5-octet dot notation)
        assert ExcelIPAMParser._normalise_subnet("10.113.64.0.24") == "10.113.64.0/24"

    def test_invalid_cidr_empty(self):
        assert ExcelIPAMParser._normalise_subnet("") == ""

    def test_invalid_cidr_string(self):
        assert ExcelIPAMParser._normalise_subnet("not-a-cidr") == ""

    def test_invalid_cidr_nan(self):
        assert ExcelIPAMParser._normalise_subnet("nan") == ""

    def test_invalid_cidr_no_slash(self):
        # Bare IP without slash is treated as /32
        assert ExcelIPAMParser._normalise_subnet("10.0.0.1") == "10.0.0.1/32"


# ============================================================================
# TEST 2: Dynamic Header Detection
# ============================================================================
class TestDynamicHeaderDetection:
    """Parser finds headers regardless of column-name variation."""

    def test_std_headers(self, tmp_path):
        """Standard column names: ip, subnet, vlan id, vlan name, site, description."""
        path = _make_explorer(
            str(tmp_path),
            {
                "SiteA": [
                    ["ip", "subnet", "vlan id", "vlan name", "site", "description"],
                    ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "SiteA", "Main office"],
                ]
            },
        )
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 1
        assert records[0]["ip_address"] == "10.0.0.1"
        assert records[0]["subnet"] == "10.0.0.0/24"
        assert records[0]["vlan_id"] == "100"
        assert records[0]["site"] == "SiteA"

    def test_vid_variant(self, tmp_path):
        """'VID' column is recognised as VLAN ID."""
        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["IP", "Network", "VID", "Name", "Location", "Purpose"],
                    ["192.168.1.1", "192.168.1.0/24", "200", "Guests", "HQ", "Guest WiFi"],
                ]
            },
        )
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 1
        assert records[0]["vlan_id"] == "200"

    def test_cidr_variant(self, tmp_path):
        """'CIDR' column is recognised as subnet."""
        path = _make_explorer(
            str(tmp_path),
            {
                "DC1": [
                    ["address", "CIDR", "vlan", "vlan name", "site", "desc"],
                    ["10.10.10.1", "10.10.10.0/24", "300", "Management", "DC1", "Mgmt"],
                ]
            },
        )
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 1
        assert records[0]["subnet"] == "10.10.10.0/24"

    def test_fallback_site_from_sheet_name(self, tmp_path):
        """When 'site' column is missing, sheet name is used as default site."""
        path = _make_explorer(
            str(tmp_path),
            {
                "BranchNYC": [
                    ["ip", "subnet", "vlan id", "vlan name"],
                    ["10.5.5.1", "10.5.5.0/24", "400", "Wireless"],
                ]
            },
        )
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 1
        assert records[0]["site"] == "BranchNYC"

    def test_multi_sheet_parsing(self, tmp_path):
        """Records from multiple sheets are aggregated and deduplicated."""
        path = _make_explorer(
            str(tmp_path),
            {
                "London": [
                    ["ip", "subnet", "vlan id", "vlan name", "site"],
                    ["10.1.1.1", "10.1.1.0/24", "10", "Corp", "London"],
                ],
                "Paris": [
                    ["ip", "subnet", "vlan id", "vlan name", "site"],
                    ["10.2.2.1", "10.2.2.0/24", "20", "Guest", "Paris"],
                ],
            },
        )
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 2
        sites = {r["site"] for r in records}
        assert "London" in sites
        assert "Paris" in sites

    def test_deduplication_across_sheets(self, tmp_path):
        """Same (ip, subnet) appearing in two sheets yields one record."""
        path = _make_explorer(
            str(tmp_path),
            {
                "SheetA": [
                    ["ip", "subnet", "vlan id", "vlan name", "site"],
                    ["10.0.0.1", "10.0.0.0/24", "100", "Test", "SiteA"],
                ],
                "SheetB": [
                    ["ip", "subnet", "vlan id", "vlan name", "site"],
                    ["10.0.0.1", "10.0.0.0/24", "100", "Test", "SiteB"],
                ],
            },
        )
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 1


# ============================================================================
# TEST 3: Classification Logic
# ============================================================================
class TestClassification:
    """Matched / Pending-Add / Conflict categorisation."""

    def test_matched_record(self, tmp_path):
        """Record in both Excel and DB with same site/VLAN/subnet → matched."""
        db = _make_db(str(tmp_path))
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO ipam_inventory (ip_address, subnet, vlan_id, vlan_name, site) VALUES (?, ?, ?, ?, ?)",
            ("10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"),
        )
        conn.commit()
        conn.close()

        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["ip", "subnet", "vlan id", "vlan name", "site"],
                    ["10.0.0.1", "10.0.0.0/24", "100", "Corporate", "HQ"],
                ]
            },
        )
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        assert len(matched) == 1
        assert len(pending) == 0
        assert len(conflicts) == 0
        dbm.close()

    def test_pending_add(self, tmp_path):
        """Record not in DB → pending add."""
        db = _make_db(str(tmp_path))
        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["ip", "subnet", "vlan id", "vlan name", "site"],
                    ["10.0.0.99", "10.0.0.0/24", "100", "Corporate", "HQ"],
                ]
            },
        )
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        assert len(matched) == 0
        assert len(pending) == 1
        assert pending[0]["ip_address"] == "10.0.0.99"
        assert pending[0]["status"] == "Active"
        dbm.close()

    def test_conflict_site_mismatch(self, tmp_path):
        """Same IP/subnet key but different site → conflict."""
        db = _make_db(str(tmp_path))
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO ipam_inventory (ip_address, subnet, vlan_id, site) VALUES (?, ?, ?, ?)",
            ("10.0.0.1", "10.0.0.0/24", "100", "HQ"),
        )
        conn.commit()
        conn.close()

        path = _make_explorer(
            str(tmp_path),
            {
                "Branch": [
                    ["ip", "subnet", "vlan id", "site"],
                    ["10.0.0.1", "10.0.0.0/24", "100", "Branch"],
                ]
            },
        )
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        assert len(conflicts) == 1
        assert "site" in conflicts[0]["conflict_reason"].lower()
        dbm.close()

    def test_conflict_vlan_mismatch(self, tmp_path):
        """Same IP but different VLAN ID → conflict."""
        db = _make_db(str(tmp_path))
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO ipam_inventory (ip_address, subnet, vlan_id, site) VALUES (?, ?, ?, ?)",
            ("10.0.0.1", "10.0.0.0/24", "100", "HQ"),
        )
        conn.commit()
        conn.close()

        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["ip", "subnet", "vlan id", "site"],
                    ["10.0.0.1", "10.0.0.0/24", "200", "HQ"],
                ]
            },
        )
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        assert len(conflicts) == 1
        assert "vlan_id" in conflicts[0]["conflict_reason"].lower()
        dbm.close()

    def test_conflict_subnet_mismatch(self, tmp_path):
        """Same IP but different subnet → conflict."""
        db = _make_db(str(tmp_path))
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO ipam_inventory (ip_address, subnet, vlan_id, site) VALUES (?, ?, ?, ?)",
            ("10.0.0.1", "10.0.0.0/24", "100", "HQ"),
        )
        conn.commit()
        conn.close()

        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["ip", "subnet", "vlan id", "site"],
                    ["10.0.0.1", "10.0.1.0/24", "100", "HQ"],
                ]
            },
        )
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        assert len(conflicts) == 1
        assert "subnet" in conflicts[0]["conflict_reason"].lower()
        dbm.close()

    def test_no_db_records_all_pending(self, tmp_path):
        """Empty DB → every Excel record is pending add."""
        db = _make_db(str(tmp_path))
        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["ip", "subnet", "vlan id", "site"],
                    ["10.0.0.1", "10.0.0.0/24", "100", "HQ"],
                    ["10.0.0.2", "10.0.0.0/24", "100", "HQ"],
                ]
            },
        )
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        assert len(pending) == 2
        assert len(matched) == 0
        assert len(conflicts) == 0
        dbm.close()

    def test_output_csv_compatible_with_netbox_uploader(self, tmp_path):
        """Pending-add CSV columns match NetBox Universal Uploader format."""
        db = _make_db(str(tmp_path))
        path = _make_explorer(
            str(tmp_path),
            {
                "HQ": [
                    ["ip", "subnet", "vlan id", "vlan name", "site", "description"],
                    ["10.0.0.5", "10.0.0.0/24", "100", "Corporate", "HQ", "Test desc"],
                ]
            },
        )
        out_dir = str(tmp_path / "output")
        dbm = LocalSQLiteManager(db)
        records = ExcelIPAMParser(path).parse()
        matched, pending, conflicts = classify_records(records, dbm)
        write_csv(os.path.join(out_dir, "ipam_pending_add.csv"), pending, [
            "ip_address", "subnet", "vlan_id", "vlan_name", "site", "description", "status"
        ])

        with open(os.path.join(out_dir, "ipam_pending_add.csv")) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["ip_address"] == "10.0.0.5"
        assert row["status"] == "Active"
        assert row["subnet"] == "10.0.0.0/24"
        dbm.close()


# ============================================================================
# TEST 4: Version Assertion
# ============================================================================
class TestVersion:
    """APP_VERSION must equal '4.3'."""

    def test_app_version_is_43(self):
        assert APP_VERSION == "4.3", f"Expected '4.3', got {APP_VERSION!r}"

    def test_version_is_not_string_v_prefix(self):
        """Version is the bare string '4.3', not 'v4.3'."""
        assert not APP_VERSION.startswith("v"), f"Version should not have 'v' prefix: {APP_VERSION!r}"


# ============================================================================
# TEST 5: Multi-Sheet with 42 Sheets (stress test)
# ============================================================================
class TestMultiSheetStress:
    """Verify parser handles many sheets without error."""

    def test_42_sheets_parsed(self, tmp_path):
        sheet_data = {}
        for i in range(42):
            site = f"Site{i:02d}"
            sheet_data[site] = [
                ["ip", "subnet", "vlan id", "vlan name", "site"],
                [f"10.{i}.0.1", f"10.{i}.0.0/24", str(100 + i), f"Role{i}", site],
            ]
        path = _make_explorer(str(tmp_path), sheet_data)
        records = ExcelIPAMParser(path).parse()
        assert len(records) == 42
        sites = {r["site"] for r in records}
        assert len(sites) == 42


# ============================================================================
# Main
# ============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
