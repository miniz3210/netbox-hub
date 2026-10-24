"""Test minimal-branch single-connection fix in save_netbox_backup."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import core.backup_manager as bm
import core.db_manager as dbm


def _make_tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="nb_test_")
    os.close(fd)
    return path


def _make_device(dev_id, name, site="SiteA"):
    return {
        "id": dev_id,
        "name": name,
        "site": site,
        "device_type": {"model": "NX-OSv 9000", "manufacturer": {"name": "Cisco"}},
        "device_role": {"name": "Firewall"},
        "status": {"value": "active"},
        "description": "Test device",
    }


def _make_ip(addr_id, address, site="SiteA"):
    return {
        "id": addr_id,
        "address": address,
        "site": site,
        "status": {"value": "active"},
    }


def _make_vlan(vlan_id, vid, name, site="SiteA"):
    return {
        "id": vlan_id,
        "vid": vid,
        "name": name,
        "site": site,
        "status": {"value": "active"},
    }


def _make_prefix(prefix_str, vlan_id=None, site="SiteA"):
    return {
        "prefix": prefix_str,
        "site": site,
        "vlan": {"id": vlan_id, "vid": vlan_id, "name": f"VLAN{vlan_id}"} if vlan_id else None,
        "status": {"value": "active"},
    }


def _make_vm(vm_id, name, site="SiteA"):
    return {
        "id": vm_id,
        "name": name,
        "site": site,
        "role": {"name": "Server"},
        "status": {"value": "active"},
        "description": "Test VM",
    }


def _make_site(site_id, name, slug=None):
    return {
        "id": site_id,
        "name": name,
        "slug": slug or name.lower().replace(" ", "-"),
        "status": {"value": "active"},
    }


def _build_full_payload():
    """Full payload with >=100 endpoints so it is classified as a real Full upload.

    Payload shape is the netbox-export.ps1 v2.0 `endpoints` array layout.
    Includes core/object-changes (50 records) and ipam/ip-addresses (20 records)
    so we can verify the minimal merge preserves/replaces them correctly.
    """
    devices = [_make_device(i, f"dev-{i:02d}") for i in range(1, 11)]       # 10
    ips = [_make_ip(i, f"10.0.{i}.1/32") for i in range(1, 21)]            # 20
    vlans = [_make_vlan(i, i, f"VLAN{i}") for i in range(1, 6)]             # 5
    prefixes = [_make_prefix(f"10.{i}.0.0/24", vlan_id=i) for i in range(1, 7)]  # 6
    vms = [_make_vm(i, f"vm-{i:02d}") for i in range(1, 8)]                # 7
    sites = [_make_site(i, f"Site-{i}") for i in range(1, 4)]              # 3
    object_changes = [{"id": i, "changed_object_id": i} for i in range(1, 51)]  # 50
    # Pad to >=100 endpoints so is_full=True
    padding = [
        {"path": "extras/tags", "records": [{"id": i, "name": f"pad{i}", "slug": f"pad{i}"}]}
        for i in range(1, 95)
    ]
    return {
        "metadata": {
            "backup_completed": "2026-09-09T10:02:01.3007400+01:00",
            "netbox_version": "4.6.8",
        },
        "endpoints": [
            {"path": "dcim/devices", "records": devices},
            {"path": "ipam/ip-addresses", "records": ips},
            {"path": "ipam/vlans", "records": vlans},
            {"path": "ipam/prefixes", "records": prefixes},
            {"path": "virtualization/virtual-machines", "records": vms},
            {"path": "dcim/sites", "records": sites},
            {"path": "core/object-changes", "records": object_changes},
        ]
        + padding,
        "summary": [],
    }


def _build_minimal_payload():
    """Minimal payload replacing only dcim/devices (8), ipam/ip-addresses (18),
    ipam/vlans (5), plus a non-SQL endpoint extras/custom-field-choice-sets (3).
    """
    devices = [_make_device(i, f"min-dev-{i:02d}") for i in range(1, 9)]    # 8
    ips = [_make_ip(i, f"192.168.{i}.1/24") for i in range(1, 19)]         # 18
    vlans = [_make_vlan(i, i + 100, f"MinVLAN{i}") for i in range(1, 6)]   # 5
    return {
        "metadata": {"backup_completed": "2026-10-23T10:00:00.000000+01:00"},
        "endpoints": [
            {"path": "dcim/devices", "records": devices},
            {"path": "ipam/ip-addresses", "records": ips},
            {"path": "ipam/vlans", "records": vlans},
            {"path": "extras/custom-field-choice-sets", "records": [{"id": i, "name": f"CS{i}"} for i in range(1, 4)]},
        ],
        "summary": [],
    }


def _build_minimal_payload_with_unverified_ipam():
    """Minimal payload where ipam/ip-addresses has verified=False.

    ipam/ip-addresses is a non-SQL endpoint (not in _ENDPOINT_TO_OBJECT_TYPE).
    With the safety rule applied, it must be skipped and the stored IP addresses
    left unchanged.
    """
    devices = [_make_device(i, f"min-dev-{i:02d}") for i in range(1, 9)]    # 8
    vlans = [_make_vlan(i, i + 100, f"MinVLAN{i}") for i in range(1, 6)]   # 5
    return {
        "metadata": {"backup_completed": "2026-10-23T11:00:00.000000+01:00"},
        "endpoints": [
            {"path": "dcim/devices", "records": devices},
            {"path": "ipam/ip-addresses", "records": [_make_ip(i, f"10.99.{i}.1/24") for i in range(1, 6)]},
            {"path": "ipam/vlans", "records": vlans},
        ],
        "summary": [
            {"path": "ipam/ip-addresses", "verified": False, "downloaded_count": 3, "expected_count": 20},
        ],
    }


@pytest.fixture
def tmpdb(request):
    path = _make_tmp_db()
    request.addfinalizer(lambda: os.unlink(path) if os.path.exists(path) else None)
    return path


@pytest.fixture(autouse=True)
def _patch_db_paths(tmpdb):
    original_bm_path = bm.DB_PATH
    original_db_path = dbm.DB_PATH
    bm.DB_PATH = tmpdb
    dbm.DB_PATH = tmpdb
    dbm._db_init_done = False
    bm.init_backup_tables()
    yield tmpdb
    bm.DB_PATH = original_bm_path
    dbm.DB_PATH = original_db_path
    dbm._db_init_done = False


@pytest.fixture(autouse=True)
def _patch_prune():
    with mock.patch.object(bm, "_prune_stale_backup_files", return_value=0):
        yield


@pytest.fixture(autouse=True)
def _patch_hints():
    with mock.patch("core.backup_manager.get_hypervisor_device_hints", return_value=[]):
        yield


class TestMinimalBranchSingleConnection:
    def test_full_then_minimal_no_lock(self, tmpdb):
        """(1)+(2) Save a full payload then a minimal one; no sqlite3.OperationalError."""
        result = bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        assert result is not None

        result = bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())
        assert result is not None

    def test_backup_records_counts(self, tmpdb):
        """(4) After minimal upload: devices=8; untouched types unchanged."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()

        counts = {}
        for ot in ("dcim_devices", "ipam_vlans",
                    "ipam_prefixes", "virtualization_virtual_machines",
                    "dcim_sites"):
            cur.execute("SELECT COUNT(*) FROM backup_records WHERE object_type = ?", (ot,))
            counts[ot] = cur.fetchone()[0]
        conn.close()

        assert counts["dcim_devices"] == 8
        assert counts["ipam_vlans"] == 5
        assert counts["ipam_prefixes"] == 6
        assert counts["virtualization_virtual_machines"] == 7
        assert counts["dcim_sites"] == 3

    def test_ipam_and_inventory_unchanged(self, tmpdb):
        """(5) ipam_records: 6 prefix rows, 5 vlan rows; inventory_records: 7 vm rows."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM ipam_records WHERE record_type = 'prefix'")
        assert cur.fetchone()[0] == 6

        cur.execute("SELECT COUNT(*) FROM ipam_records WHERE record_type = 'vlan'")
        assert cur.fetchone()[0] == 5

        cur.execute("SELECT COUNT(*) FROM inventory_records WHERE category = 'vm'")
        assert cur.fetchone()[0] == 7
        conn.close()

    def test_backup_endpoints_blob(self, tmpdb):
        """(6) backup_endpoints contains dcim/devices (8) with kind=minimal."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()

        cur.execute(
            "SELECT path, record_count, kind FROM backup_endpoints "
            "WHERE path IN ('dcim/devices', 'ipam/vlans')"
        )
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        conn.close()

        assert rows["dcim/devices"] == (8, "minimal")
        assert rows["ipam/vlans"] == (5, "minimal")

    def test_rollback_on_insert_failure(self, tmpdb):
        """(7) Monkeypatch _scoped_insert_backup_rows to raise after scoped delete.
        Exception propagates and table counts are identical to before the upload."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        pre_counts = {}
        for ot in ("dcim_devices", "ipam_vlans"):
            cur.execute("SELECT COUNT(*) FROM backup_records WHERE object_type = ?", (ot,))
            pre_counts[ot] = cur.fetchone()[0]
        conn.close()

        original_insert = bm._scoped_insert_backup_rows

        def raising_insert(conn, object_type, rows, uploaded_at):
            if object_type == "dcim_devices":
                raise RuntimeError("simulated insert failure")
            return original_insert(conn, object_type, rows, uploaded_at)

        with pytest.raises(RuntimeError, match="simulated insert failure"):
            with mock.patch.object(bm, "_scoped_insert_backup_rows", raising_insert):
                bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        post_counts = {}
        for ot in ("dcim_devices", "ipam_vlans"):
            cur.execute("SELECT COUNT(*) FROM backup_records WHERE object_type = ?", (ot,))
            post_counts[ot] = cur.fetchone()[0]
        conn.close()

        assert post_counts == pre_counts

    def test_no_orphan_transaction_after_upload(self, tmpdb):
        """(8) After upload, a fresh connection can acquire BEGIN IMMEDIATE immediately."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())

        fresh = sqlite3.connect(tmpdb)
        fresh.execute("SELECT 1")
        fresh.execute("BEGIN IMMEDIATE")
        fresh.execute("ROLLBACK")
        fresh.close()

    def test_merged_blob_preserves_full_endpoints(self, tmpdb):
        """(i)+(ii)+(iii) After Full then Minimal, the BLOB is the Full payload with
        only the replaced endpoints swapped.  core/object-changes stays at 50,
        ipam/ip-addresses gets the Minimal's count (18)."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full")
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode(), filename="Minimal")

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()

        assert row is not None and row[0] is not None, "blob must exist after Full+Minimal"

        import gzip as _gzip
        merged = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        endpoints_map = {
            ep["path"]: len(ep.get("records", []))
            for ep in merged.get("endpoints", [])
        }

        # (i) Every Full endpoint still present
        full = json.loads(json.dumps(_build_full_payload()))
        for ep in full["endpoints"]:
            path = ep["path"]
            full_count = len(ep["records"])
            assert path in endpoints_map, f"Blob missing endpoint {path}"
            if path not in ("dcim/devices", "ipam/ip-addresses", "ipam/vlans",
                            "extras/custom-field-choice-sets"):
                assert endpoints_map[path] == full_count, \
                    f"{path} should keep Full count {full_count}, got {endpoints_map[path]}"

        # (ii) ipam/ip-addresses has the Minimal's count
        assert endpoints_map.get("ipam/ip-addresses") == 18, \
            f"ipam/ip-addresses should be 18, got {endpoints_map.get('ipam/ip-addresses')}"

        # (iii) core/object-changes still has 50 records
        assert endpoints_map.get("core/object-changes") == 50, \
            f"core/object-changes should be 50, got {endpoints_map.get('core/object-changes')}"

    def test_metadata_keeps_full_filename(self, tmpdb):
        """Full filename preserved; last_minimal_file and timestamp recorded."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full")
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode(), filename="Minimal")

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute(
            "SELECT filename, last_minimal_file, last_minimal_uploaded_at FROM backup_metadata WHERE id = 1"
        )
        row = cur.fetchone()
        conn.close()

        assert row[0] == "Full"
        assert row[1] == "Minimal"
        assert row[2] is not None and row[2] != ""

    def test_restore_from_merged_blob(self, tmpdb):
        """After Full then Minimal, restore reads the merged blob and populates
        the registry with the correct per-endpoint counts."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())

        from core.shared_backup_state import SharedBackupState
        SharedBackupState.initialize()
        import streamlit as st
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {}

        bm.restore_backup_to_session_state()

        registry = st.session_state.get(SharedBackupState.OBJECT_REGISTRY_KEY, {})

        dcim_devices = registry.get("dcim/devices", {})
        assert dcim_devices.get("count") == 8, f"expected dcim/devices=8, got {dcim_devices}"

        core_oc = registry.get("core/object-changes", {})
        assert core_oc.get("count") == 50, f"expected core/object-changes=50, got {core_oc}"

        ipam_ips = registry.get("ipam/ip-addresses", {})
        assert ipam_ips.get("count") == 18, f"expected ipam/ip-addresses=18, got {ipam_ips}"

    def test_unverified_non_sql_endpoint_not_replaced(self, tmpdb):
        """A minimal whose ipam/ip-addresses has verified=False must leave
        the stored ip-addresses unchanged.  ipam/ip-addresses is a non-SQL
        endpoint and should be skipped by the same safety rules."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        # Verify the full payload's ip-addresses are in the blob (non-SQL)
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()
        assert row is not None and row[0] is not None
        import gzip as _gzip
        stored = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        ep_map = {ep["path"]: len(ep.get("records", [])) for ep in stored.get("endpoints", [])}
        assert ep_map.get("ipam/ip-addresses") == 20, \
            f"expected 20 IP addresses in blob after full, got {ep_map.get('ipam/ip-addresses')}"

        # Upload minimal with verified=False on ipam/ip-addresses
        result = bm.save_netbox_backup(json.dumps(_build_minimal_payload_with_unverified_ipam()).encode())

        # The result should report ipam/ip-addresses as skipped
        skipped = result.get("skipped", {})
        assert "ipam/ip-addresses" in skipped, f"expected ipam/ip-addresses in skipped, got {skipped}"

        # The stored IP address count in the blob must remain 20 (not replaced)
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()
        stored = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        ep_map = {ep["path"]: len(ep.get("records", [])) for ep in stored.get("endpoints", [])}
        assert ep_map.get("ipam/ip-addresses") == 20, \
            f"expected 20 IP addresses preserved in blob, got {ep_map.get('ipam/ip-addresses')}"

    def test_layers_keyed_by_endpoint_path(self, tmpdb):
        """After Full then Minimal, the merged blob's layers dict is keyed by
        endpoint path (e.g. 'dcim/devices'), not by filename.  Each entry has
        file, exported_at, and kind fields."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full")
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode(), filename="Minimal")

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()

        assert row is not None and row[0] is not None
        import gzip as _gzip
        merged = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        layers = merged.get("layers")
        assert isinstance(layers, dict), f"expected layers dict, got {type(layers)}"

        # Should be keyed by endpoint path, not filename
        assert "dcim/devices" in layers, f"expected 'dcim/devices' key in layers, got {list(layers.keys())}"
        assert "ipam/vlans" in layers, f"expected 'ipam/vlans' key in layers, got {list(layers.keys())}"
        assert "Minimal" not in layers, f"filename key 'Minimal' should not be in layers, got {list(layers.keys())}"

        for path, entry in layers.items():
            assert "file" in entry, f"layer entry for {path} missing 'file'"
            assert "exported_at" in entry, f"layer entry for {path} missing 'exported_at'"
            assert "kind" in entry, f"layer entry for {path} missing 'kind'"
            assert entry["kind"] == "minimal"

    def test_full_upload_resets_layers(self, tmpdb):
        """A Full upload resets the layers dict to {}."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full")
        bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode(), filename="Minimal")
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full2")

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()

        assert row is not None and row[0] is not None
        import gzip as _gzip
        merged = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        layers = merged.get("layers")
        assert layers == {}, f"expected empty layers after full upload, got {layers}"

    def test_downloaded_count_lt_expected_count_skips(self, tmpdb):
        """(b) downloaded_count < expected_count without a verified flag → skipped."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        payload = _build_minimal_payload_with_unverified_ipam()
        # Remove verified flag but keep downloaded_count < expected_count
        payload["summary"] = [
            {"path": "ipam/ip-addresses", "downloaded_count": 3, "expected_count": 20},
        ]
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        skipped = result.get("skipped", {})
        assert "ipam/ip-addresses" in skipped, f"expected skip, got {skipped}"

    def test_zero_records_with_expected_replaces_zero_expected(self, tmpdb):
        """(c) 0 records with expected_count=0 replaces (genuine removal);
        0 records with expected_count=None is skipped as suspicious."""
        import gzip as _gzip
        # --- genuine removal: 0 records, expected_count=0 ---
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())
        payload_remove = {
            "metadata": {"backup_completed": "2026-10-23T12:00:00.000000+01:00"},
            "endpoints": [
                {"path": "dcim/devices", "records": [], "expected_count": 0},
                {"path": "ipam/vlans", "records": [_make_vlan(1, 1, "V1")]},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(payload_remove).encode())
        skipped = result.get("skipped", {})
        assert "dcim/devices" not in skipped, f"expected replace for expected_count=0, got skipped={skipped}"
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()
        stored = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        ep_map = {ep["path"]: len(ep.get("records", [])) for ep in stored.get("endpoints", [])}
        assert ep_map.get("dcim/devices") == 0, f"expected 0 devices after removal, got {ep_map.get('dcim/devices')}"

        # --- suspicious empty: 0 records, expected_count=None on ipam/vlans ---
        # Must include at least one SQL endpoint with records so _bucket_full_walk succeeds
        payload_suspicious = {
            "metadata": {"backup_completed": "2026-10-23T13:00:00.000000+01:00"},
            "endpoints": [
                {"path": "ipam/vlans", "records": [], "expected_count": None},
                {"path": "dcim/sites", "records": [_make_site(1, "SiteA")]},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(payload_suspicious).encode())
        skipped = result.get("skipped", {})
        assert "ipam/vlans" in skipped, f"expected skip suspicious empty, got {skipped}"

    def test_non_sql_endpoint_with_verified_false_untouched(self, tmpdb):
        """(d) A non-SQL endpoint such as extras/custom-field-choice-sets
        with verified=False leaves the stored copy untouched."""
        import gzip as _gzip
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        # Check blob has extras/custom-field-choice-sets from padding
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()
        stored = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        ep_map = {ep["path"]: len(ep.get("records", [])) for ep in stored.get("endpoints", [])}
        pre_count = ep_map.get("extras/tags", 0)
        assert pre_count > 0, "expected extras/tags in blob"

        # Upload minimal with verified=False on extras/tags
        payload = {
            "metadata": {"backup_completed": "2026-10-23T14:00:00.000000+01:00"},
            "endpoints": [
                {"path": "extras/tags", "records": [{"id": 999, "name": "new-tag", "slug": "new-tag"}]},
            ],
            "summary": [
                {"path": "extras/tags", "verified": False, "downloaded_count": 1, "expected_count": 5},
            ],
        }
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        skipped = result.get("skipped", {})
        assert "extras/tags" in skipped, f"expected extras/tags skipped, got {skipped}"

        # Blob should be unchanged
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()
        stored = json.loads(_gzip.decompress(row[0]).decode("utf-8"))
        ep_map = {ep["path"]: len(ep.get("records", [])) for ep in stored.get("endpoints", [])}
        assert ep_map.get("extras/tags") == pre_count, \
            f"expected {pre_count} extras/tags preserved, got {ep_map.get('extras/tags')}"
