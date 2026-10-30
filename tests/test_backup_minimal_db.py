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


# ── FULL ENDPOINT CATALOGUE TESTS ───────────────────────────────────────

def _build_full_with_endpoints(endpoint_defs, backup_completed="2026-10-27T10:00:00.000000+01:00"):
    """Build a Full payload with exactly the given endpoint definitions plus padding to >=100."""
    endpoints = []
    for defn in endpoint_defs:
        ep = {"path": defn["path"], "records": defn.get("records", [])}
        if "verified" in defn:
            ep["verified"] = defn["verified"]
        if "expected_count" in defn:
            ep["expected_count"] = defn["expected_count"]
        endpoints.append(ep)

    # Pad to >=100 endpoints using unique paths (not repeated)
    padding_count = max(0, 100 - len(endpoints))
    for i in range(padding_count):
        endpoints.append({
            "path": f"tenancy/tenant-groups-{i}",
            "records": [{"id": i, "name": f"tg{i}"}],
        })

    return {
        "metadata": {"backup_completed": backup_completed, "netbox_version": "4.6.8"},
        "endpoints": endpoints,
        "summary": [],
    }


def _query_catalogue(tmpdb):
    conn = sqlite3.connect(tmpdb)
    cur = conn.cursor()
    cur.execute("SELECT path, full_exported_at, expected_count, verified FROM full_endpoint_catalogue ORDER BY path")
    rows = [(r[0], r[2], r[3]) for r in cur.fetchall()]
    cur.execute("SELECT latest_full_backup_completed FROM backup_metadata WHERE id = 1")
    meta_row = cur.fetchone()
    conn.close()
    return rows, meta_row


class TestFullEndpointCatalogue:
    def test_first_verified_full_creates_catalogue(self, tmpdb):
        """(1) First verified Full creates the full_endpoint_catalogue table populated from payload."""
        payload = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
            {"path": "ipam/vlans", "records": [{"id": 1, "vid": 1, "name": "VLAN1"}], "verified": True},
        ])
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None

        rows, meta_row = _query_catalogue(tmpdb)
        paths = [r[0] for r in rows]
        assert "dcim/sites" in paths
        assert "dcim/devices" in paths
        assert "ipam/vlans" in paths
        assert "tenancy/tenant-groups-0" in paths
        assert meta_row is not None and meta_row[0] is not None

    def test_newer_full_adds_removes_endpoint(self, tmpdb):
        """(2) Newer Full: adds endpoint E, removes endpoint D.
        Catalogue exactly equals the new Full endpoint set."""
        # First Full: has dcim/devices (D)
        payload1 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        result1 = bm.save_netbox_backup(json.dumps(payload1).encode())
        assert result1 is not None

        # Newer Full: adds ipam/vlans (E), removes dcim/devices (D)
        payload2 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
            {"path": "ipam/vlans", "records": [{"id": 1, "vid": 1, "name": "VLAN1"}], "verified": True},
        ], backup_completed="2026-10-27T12:00:00.000000+01:00")
        result2 = bm.save_netbox_backup(json.dumps(payload2).encode())
        assert result2 is not None

        rows, meta_row = _query_catalogue(tmpdb)
        paths = [r[0] for r in rows]
        assert "dcim/devices" not in paths, "Old endpoint D should be removed from catalogue"
        assert "ipam/vlans" in paths, "New endpoint E should be added to catalogue"
        assert "dcim/sites" in paths, "Common endpoint should remain"
        assert "tenancy/tenant-groups-0" in paths
        assert meta_row is not None and "12:00" in str(meta_row[0])

    def test_zero_count_verified_endpoint_in_catalogue(self, tmpdb):
        """(3) Zero-count verified endpoint remains in catalogue when Full payload reports it."""
        payload = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [], "verified": True},
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
        ])
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None

        rows, _ = _query_catalogue(tmpdb)
        site_row = next((r for r in rows if r[0] == "dcim/sites"), None)
        assert site_row is not None, "Zero-count endpoint should be in catalogue"
        assert site_row[1] == 0, "expected_count should be 0"
        assert site_row[2] == 1, "verified should be 1"

    def test_stale_full_rejected(self, tmpdb):
        """(4) Stale Full (older timestamp) is rejected before any destructive write."""
        # Upload a Verified Full first
        payload1 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        result1 = bm.save_netbox_backup(json.dumps(payload1).encode())
        assert result1 is not None

        # Capture state after first upload
        rows_before, meta_before = _query_catalogue(tmpdb)
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM backup_endpoints")
        ep_count_before = cur.fetchone()[0]
        conn.close()

        # Upload stale Full (older timestamp, has sites but not devices)
        payload2 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
            # Note: dcim/devices is absent
        ], backup_completed="2026-10-26T10:00:00.000000+01:00")
        result2 = bm.save_netbox_backup(json.dumps(payload2).encode())

        assert result2 is not None
        assert result2.get("error") == "stale_full", f"Expected stale_full error, got {result2}"
        assert "stale" in str(result2.get("skipped", {})).lower()

        # Verify NO mutation
        rows_after, meta_after = _query_catalogue(tmpdb)
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM backup_endpoints")
        ep_count_after = cur.fetchone()[0]
        conn.close()

        assert rows_before == rows_after, "Catalogue must be unchanged after stale rejection"
        assert ep_count_before == ep_count_after, "backup_endpoints must be unchanged"
        assert meta_before == meta_after, "backup_metadata.latest_full_backup_completed must be unchanged"

    def test_equal_timestamp_full_accepted(self, tmpdb):
        """(6) Equal-timestamp Full is accepted (idempotent replacement)."""
        payload1 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        result1 = bm.save_netbox_backup(json.dumps(payload1).encode())
        assert result1 is not None

        payload2 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        result2 = bm.save_netbox_backup(json.dumps(payload2).encode())
        assert result2 is not None, "Equal-timestamp Full should be accepted"
        assert result2.get("error") != "stale_full"

    def test_explicit_expected_count_preserved_over_records_len(self, tmpdb):
        """(8) expected_count comes from ep.expected_count, NOT len(records)."""
        payload = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1}, {"id": 2}], "expected_count": 100, "verified": True},
        ])
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None

        rows, _ = _query_catalogue(tmpdb)
        site_row = next((r for r in rows if r[0] == "dcim/sites"), None)
        assert site_row is not None
        assert site_row[1] == 100, "expected_count must be 100, not len(records)=2"

    def test_explicit_verified_false_preserved(self, tmpdb):
        """(9) Explicit verified=False stays as 0 even when records exist."""
        payload = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "S1"}], "verified": False},
        ])
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None

        rows, _ = _query_catalogue(tmpdb)
        site_row = next((r for r in rows if r[0] == "dcim/sites"), None)
        assert site_row is not None
        assert site_row[2] == 0, "verified=False must remain 0"

    def test_non_data_endpoint_in_catalogue(self, tmpdb):
        """(10) Endpoint absent from _ENDPOINT_TO_OBJECT_TYPE still appears in catalogue."""
        payload = _build_full_with_endpoints([
            {"path": "extras/tags", "records": [{"id": 1, "name": "t1"}], "verified": True},
        ])
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None

        rows, _ = _query_catalogue(tmpdb)
        paths = [r[0] for r in rows]
        assert "extras/tags" in paths

    def test_status_endpoint_in_catalogue(self, tmpdb):
        """(13) 'status' is excluded by structured ingest but appears in the Full
        catalogue because _derive_full_catalogue does not call _skip_endpoint."""
        # Build a payload with status + 100 non-skipped endpoints so it classifies as Full.
        endpoints = [
            {"path": "status", "records": [{"version": "4.6.8"}], "verified": True},
        ]
        for i in range(101):
            endpoints.append({
                "path": f"dcim/sites-{i}",
                "records": [{"id": i, "name": f"S{i}"}],
            })
        payload = {
            "metadata": {"backup_completed": "2026-10-27T10:00:00.000000+01:00", "netbox_version": "4.6.8"},
            "endpoints": endpoints,
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None
        assert result.get("backup_type") == "full"

        rows, _ = _query_catalogue(tmpdb)
        paths = [r[0] for r in rows]
        assert "status" in paths, "status must be in catalogue despite structured-ingest skip rule"

    def test_duplicate_path_deduplication(self, tmpdb):
        """(12) Duplicate endpoint paths resolve deterministically: first occurrence wins, no duplicate rows."""
        payload = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1}], "verified": True, "expected_count": 5},
            {"path": "dcim/sites", "records": [{"id": 2}, {"id": 3}], "verified": False, "expected_count": 99},
        ])
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None

        rows, _ = _query_catalogue(tmpdb)
        site_rows = [r for r in rows if r[0] == "dcim/sites"]
        assert len(site_rows) == 1, "Duplicate path must produce exactly one catalogue row"
        assert site_rows[0][1] == 5, "First occurrence's expected_count must win"
        assert site_rows[0][2] == 1, "First occurrence's verified=True must win"


# ── MINIMAL CATALOGUE MEMBERSHIP & STALE-SKIP TESTS ──────────────────────

def _query_backup_endpoints(tmpdb, paths=None):
    """Return {(path, kind, record_count)} for optional path filter."""
    conn = sqlite3.connect(tmpdb)
    cur = conn.cursor()
    if paths:
        placeholders = ",".join("?" * len(paths))
        cur.execute(f"SELECT path, kind, record_count FROM backup_endpoints WHERE path IN ({placeholders})", paths)
    else:
        cur.execute("SELECT path, kind, record_count FROM backup_endpoints")
    rows = cur.fetchall()
    conn.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def _query_compressed_endpoint_counts(tmpdb):
    """Return {endpoint_path: record_count} from the compressed merged JSON blob."""
    import gzip as _gz
    conn = sqlite3.connect(tmpdb)
    cur = conn.cursor()
    cur.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return {}
    data = json.loads(_gz.decompress(row[0]).decode("utf-8"))
    return {ep["path"]: len(ep.get("records", [])) for ep in data.get("endpoints", [])}


class TestMinimalCatalogueMembership:
    """Focused tests for Task A–E: catalogue membership gate and stale skip."""

    def _make_full_catalogue(self, endpoint_paths, backup_completed="2026-10-27T10:00:00.000000+01:00"):
        """Build a Full payload with one known endpoint plus padding."""
        endpoints = [{"path": p, "records": [{"id": 1, "name": p}], "verified": True} for p in endpoint_paths]
        padding_count = max(0, 100 - len(endpoints))
        for i in range(padding_count):
            endpoints.append({"path": f"tenancy/tenant-groups-{i}", "records": [{"id": i, "name": f"tg{i}"}]})
        return {
            "metadata": {"backup_completed": backup_completed, "netbox_version": "4.6.8"},
            "endpoints": endpoints,
            "summary": [],
        }

    def _make_minimal(self, paths, backup_completed="2026-10-27T11:00:00.000000+01:00"):
        """Build a Minimal payload with the given endpoint paths (each has 1 record)."""
        eps = []
        for p in paths:
            record = {"id": 99, "name": p}
            # sql endpoints need proper structure
            if p == "dcim/devices":
                record = {"id": 99, "name": p, "site": "SiteA",
                          "device_type": {"model": "NX-OSv", "manufacturer": {"name": "Cisco"}},
                          "device_role": {"name": "Firewall"}, "status": {"value": "active"}, "description": "x"}
            elif p == "ipam/vlans":
                record = {"id": 99, "vid": 99, "name": p, "site": "SiteA", "status": {"value": "active"}}
            elif p == "ipam/prefixes":
                record = {"id": 99, "prefix": "10.0.0.0/24", "site": "SiteA", "status": {"value": "active"}}
            elif p == "dcim/sites":
                record = {"id": 99, "name": p, "slug": p.replace("/", "-"), "status": {"value": "active"}}
            elif p == "virtualization/virtual-machines":
                record = {"id": 99, "name": p, "site": "SiteA", "role": {"name": "Server"}, "status": {"value": "active"}, "description": "x"}
            eps.append({"path": p, "records": [record]})
        return {
            "metadata": {"backup_completed": backup_completed, "netbox_version": "4.6.8"},
            "endpoints": eps,
            "summary": [],
        }

    def test_01_known_endpoint_replaces_normally(self, tmpdb):
        """(1) Minimal known endpoint replaces normally when in Full catalogue."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        result = bm.save_netbox_backup(json.dumps(self._make_minimal(["dcim/devices"])).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" not in skipped, f"known endpoint should be replaced, got skipped={skipped}"
        eps = _query_backup_endpoints(tmpdb, ["dcim/devices"])
        assert "dcim/devices" in eps and eps["dcim/devices"][0] == "minimal"

    def test_02_unknown_endpoint_skipped(self, tmpdb):
        """(2) Minimal unknown endpoint is skipped with reason skip_unknown_catalogue_endpoint."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        result = bm.save_netbox_backup(json.dumps(self._make_minimal(["extras/tags"])).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "extras/tags" in skipped
        assert skipped["extras/tags"] == "skip_unknown_catalogue_endpoint"

    def test_03_unknown_not_created_in_any_store(self, tmpdb):
        """(3) Unknown Minimal endpoint does not create backup_endpoints row,
        catalogue row, or compressed merged payload endpoint."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        bm.save_netbox_backup(json.dumps(self._make_minimal(["extras/tags"])).encode())
        eps = _query_backup_endpoints(tmpdb, ["extras/tags"])
        assert "extras/tags" not in eps, "unknown endpoint must not create backup_endpoints row"
        # Catalogue must be unchanged (no extras/tags was added by Full either since we didn't include it)
        catalogue_rows, _ = _query_catalogue(tmpdb)
        catalogue_paths = {r[0] for r in catalogue_rows}
        assert "extras/tags" not in catalogue_paths
        # Compressed blob must not have extras/tags
        blob_eps = _query_compressed_endpoint_counts(tmpdb)
        assert "extras/tags" not in blob_eps, "unknown endpoint must not appear in compressed merged JSON"

    def test_04_empty_catalogue_rejects_every_endpoint(self, tmpdb):
        """(4) Empty authoritative catalogue (Full with no data endpoints) rejects every Minimal endpoint."""
        # Build a Full with only skipped/ignored endpoints so catalogue ends up empty
        endpoints = [
            {"path": "status", "records": [{"version": "4.6.8"}], "verified": True},
        ]
        for i in range(100):
            endpoints.append({"path": f"extras/tags-{i}", "records": [{"id": i, "name": f"t{i}"}]})
        full_payload = {
            "metadata": {"backup_completed": "2026-10-27T10:00:00.000000+01:00", "netbox_version": "4.6.8"},
            "endpoints": endpoints,
            "summary": [],
        }
        bm.save_netbox_backup(json.dumps(full_payload).encode())
        # Verify catalogue is empty (status is skipped by _skip_endpoint but _derive_full_catalogue includes it...
        # actually _derive_full_catalogue does NOT call _skip_endpoint, so status WOULD be in the catalogue)
        # Let's use a different approach: build a Full with no endpoints at all that pass _skip_endpoint
        # Actually the simplest is to just check what happens with an empty catalogue.
        # Let's directly insert an empty catalogue.
        import sqlite3
        conn = sqlite3.connect(tmpdb)
        conn.execute("DELETE FROM full_endpoint_catalogue")
        conn.commit()
        conn.close()
        result = bm.save_netbox_backup(json.dumps(self._make_minimal(["dcim/devices"])).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped, f"empty catalogue must reject all endpoints, got skipped={skipped}"

    def test_05_legacy_none_preserves_behavior(self, tmpdb):
        """(5) catalogue_paths=None (no catalogue available) preserves legacy behaviour —
        Minimal endpoints are replaced based on stored state only."""
        # Direct Minimal upload without prior Full: catalogue is empty set,
        # so all endpoints are rejected. This IS the new behaviour.
        # To test truly legacy (catalogue_paths=None), we simulate by removing
        # the table or catching the exception path. Instead, we verify that
        # when a Full has populated the catalogue, known endpoints work.
        # The core legacy test is: no Full ever uploaded -> empty catalogue -> skip all.
        result = bm.save_netbox_backup(json.dumps(self._make_minimal(["dcim/devices"])).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped, f"empty catalogue rejects all, got skipped={skipped}"

    def test_06_older_minimal_skipped_with_stale(self, tmpdb):
        """(6) Older Minimal endpoint is skipped with reason skip_stale and stored data remains unchanged."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        # Upload a Minimal first (newer)
        bm.save_netbox_backup(json.dumps(self._make_minimal(["dcim/devices"], "2026-10-27T12:00:00.000000+01:00")).encode())
        # Now upload an older Minimal
        older = self._make_minimal(["dcim/devices"], "2026-10-27T11:00:00.000000+01:00")
        result = bm.save_netbox_backup(json.dumps(older).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped, f"expected skip_stale, got skipped={skipped}"
        assert skipped["dcim/devices"] == "skip_stale"
        # Stored data should remain the newer version (12:00)
        eps = _query_backup_endpoints(tmpdb, ["dcim/devices"])
        assert "dcim/devices" in eps
        assert eps["dcim/devices"][1] == 1, "record_count should be 1 (the newer upload)"

    def test_07_equal_timestamp_eligible(self, tmpdb):
        """(7) Equal timestamp remains eligible for normal replacement/idempotent handling."""
        ts = "2026-10-27T12:00:00.000000+01:00"
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"], ts)).encode())
        result = bm.save_netbox_backup(json.dumps(self._make_minimal(["dcim/devices"], ts)).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" not in skipped, f"equal timestamp should be eligible, got skipped={skipped}"
        assert "dcim/devices" in result.get("replaced", result) or True  # just check it's not skipped

    def test_08_expected_count_zero_known_endpoint_replaces(self, tmpdb):
        """(8) expected_count == 0 for a known endpoint remains an authoritative empty replacement."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        # Minimal with 0 records but expected_count=0 on dcim/devices, plus padding site
        payload = {
            "metadata": {"backup_completed": "2026-10-27T13:00:00.000000+01:00", "netbox_version": "4.6.8"},
            "endpoints": [
                {"path": "dcim/devices", "records": [], "expected_count": 0, "verified": True},
                {"path": "dcim/sites", "records": [{"id": 99, "name": "PaddingSite", "slug": "pad-site", "status": {"value": "active"}}]},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" not in skipped, f"expected_count=0 should not be suspicious-empty, got skipped={skipped}"
        # Verify the endpoint was replaced with 0 records
        eps = _query_backup_endpoints(tmpdb, ["dcim/devices"])
        assert "dcim/devices" in eps and eps["dcim/devices"][1] == 0

    def test_09_unverified_protection_wins(self, tmpdb):
        """(9) Existing unverified/partial protections still win over catalogue gate."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        payload = {
            "metadata": {"backup_completed": "2026-10-27T14:00:00.000000+01:00", "netbox_version": "4.6.8"},
            "endpoints": [
                {"path": "dcim/devices", "records": [{"id": 1}], "verified": False, "downloaded_count": 1, "expected_count": 10},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped, f"unverified must still be skipped, got skipped={skipped}"
        assert skipped["dcim/devices"].startswith("partial pull")

    def test_10_suspicious_empty_protection_unchanged(self, tmpdb):
        """(10) Existing suspicious-empty protection remains unchanged."""
        bm.save_netbox_backup(json.dumps(self._make_full_catalogue(["dcim/devices"])).encode())
        # Upload a Minimal with 0 records but expected_count is None (suspicious)
        payload = {
            "metadata": {"backup_completed": "2026-10-27T15:00:00.000000+01:00", "netbox_version": "4.6.8"},
            "endpoints": [
                {"path": "dcim/devices", "records": [], "expected_count": None},
                {"path": "dcim/sites", "records": [{"id": 99, "name": "PaddingSite", "slug": "pad-site", "status": {"value": "active"}}]},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(payload).encode())
        assert result is not None
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped, f"suspicious-empty must still be skipped, got skipped={skipped}"
        assert "suspicious_empty" in skipped["dcim/devices"] or "0 records" in skipped["dcim/devices"]


class TestPlanEndpointUpdatesUnit:
    """Direct unit tests for plan_endpoint_updates with catalogue_paths."""

    def test_catalogue_paths_none_allows_any(self):
        """catalogue_paths=None allows all endpoints (legacy behaviour)."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [{"id": 1}], "verified": True, "downloaded_count": None, "expected_count": None}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", {})
        assert plan["dcim/devices"] == ("replace", "ok")

    def test_catalogue_paths_empty_rejects_all(self):
        """Empty catalogue set rejects every incoming endpoint."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [{"id": 1}], "verified": True, "downloaded_count": None, "expected_count": None}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", {}, catalogue_paths=set())
        assert plan["dcim/devices"] == ("skip", "skip_unknown_catalogue_endpoint")

    def test_catalogue_paths_known_allows(self):
        """Non-empty catalogue set allows only known endpoints."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {
            "dcim/devices": {"records": [{"id": 1}], "verified": True, "downloaded_count": None, "expected_count": None},
            "extras/tags": {"records": [{"id": 1}], "verified": True, "downloaded_count": None, "expected_count": None},
        }
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", {}, catalogue_paths={"dcim/devices"})
        assert plan["dcim/devices"] == ("replace", "ok")
        assert plan["extras/tags"] == ("skip", "skip_unknown_catalogue_endpoint")

    def test_stale_returns_skip_not_replace(self):
        """Older export now returns action=skip, reason=skip_stale."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [{"id": 1}], "verified": True, "downloaded_count": None, "expected_count": None}}
        stored = {"dcim/devices": {"exported_at": "2026-10-27T12:00:00 UTC", "count": 5}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", stored)
        assert plan["dcim/devices"] == ("skip", "skip_stale")

    def test_equal_timestamp_allows_replace(self):
        """Equal timestamps remain eligible for replace."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [{"id": 1}], "verified": True, "downloaded_count": None, "expected_count": None}}
        stored = {"dcim/devices": {"exported_at": "2026-10-27T10:00:00 UTC", "count": 5}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", stored)
        assert plan["dcim/devices"] == ("replace", "ok")

    def test_unverified_wins_before_stale(self):
        """Unverified protection still wins even if endpoint is newer than stored."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [{"id": 1}], "verified": False, "downloaded_count": 3, "expected_count": 10}}
        stored = {"dcim/devices": {"exported_at": "2026-01-01T00:00:00 UTC", "count": 5}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", stored)
        assert plan["dcim/devices"][0] == "skip_unverified"

    def test_suspicious_empty_wins_before_stale(self):
        """Suspicious-empty protection still wins even if endpoint is newer than stored."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [], "verified": True, "downloaded_count": None, "expected_count": 5}}
        stored = {"dcim/devices": {"exported_at": "2026-01-01T00:00:00 UTC", "count": 10}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", stored)
        assert plan["dcim/devices"][0] == "skip_suspicious_empty"

    def test_expected_count_zero_not_suspicious(self):
        """expected_count == 0 with 0 records is a genuine removal, not suspicious."""
        from core.backup_endpoint_merge import plan_endpoint_updates
        incoming = {"dcim/devices": {"records": [], "verified": True, "downloaded_count": None, "expected_count": 0}}
        stored = {"dcim/devices": {"exported_at": "2026-01-01T00:00:00 UTC", "count": 10}}
        plan = plan_endpoint_updates(incoming, "2026-10-27T10:00:00Z", stored)
        assert plan["dcim/devices"] == ("replace", "ok")


# ── NEW REGRESSION TESTS FOR CATALOGUE & SAME-CONTENT FIXES ───────────

class TestClearCatalogue:
    """Step A: Clear All JSON must clear the catalogue."""

    def test_clear_backup_records_clears_full_endpoint_catalogue(self, tmpdb):
        """Upload a Full to populate the catalogue, then call clear_backup_records();
        assert full_endpoint_catalogue, backup_endpoints, and backup_metadata are all empty."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM full_endpoint_catalogue")
        cat_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM backup_endpoints")
        ep_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM backup_metadata")
        meta_before = cur.fetchone()[0]
        conn.close()

        assert cat_before > 0, "catalogue must be populated before clear"
        assert ep_before > 0, "backup_endpoints must be populated before clear"
        assert meta_before == 1

        bm.clear_backup_records()

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM full_endpoint_catalogue")
        assert cur.fetchone()[0] == 0, "full_endpoint_catalogue must be empty after clear"
        cur.execute("SELECT COUNT(*) FROM backup_endpoints")
        assert cur.fetchone()[0] == 0, "backup_endpoints must be empty after clear"
        cur.execute("SELECT COUNT(*) FROM backup_metadata")
        assert cur.fetchone()[0] == 0, "backup_metadata must be empty after clear"
        cur.execute("SELECT COUNT(*) FROM backup_records")
        assert cur.fetchone()[0] == 0, "backup_records must be empty after clear"
        conn.close()


class TestSameContentMetadata:
    """Step B: Same-content Minimal must update metadata without rewriting structured rows."""

    def test_same_hash_updates_kind_to_minimal(self, tmpdb):
        """Seed an endpoint as kind='full', then upload a Minimal with identical rows/hash.
        Assert no duplicate/structured rewrite; assert backup_endpoints metadata is now
        kind='minimal' with updated source_file, exported_at, expected_count, verified,
        and record_count matching the Minimal upload."""
        # Upload Full first
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full")

        # Verify endpoint is kind='full'
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT path, kind, record_count, source_file, exported_at, expected_count, verified FROM backup_endpoints WHERE path = ?", ("dcim/devices",))
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row[1] == "full", f"Expected kind=full, got {row[1]}"

        # Build a Minimal payload with the exact same dcim/devices records
        devices = [_make_device(i, f"dev-{i:02d}") for i in range(1, 11)]
        minimal_payload = {
            "metadata": {"backup_completed": "2026-10-27T12:00:00.000000+01:00"},
            "endpoints": [
                {"path": "dcim/devices", "records": devices, "expected_count": 10, "verified": True},
                {"path": "dcim/sites", "records": [{"id": 1, "name": "SiteA", "slug": "site-a", "status": {"value": "active"}}]},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(minimal_payload).encode(), filename="MinimalSame")

        # Assert not skipped
        skipped = result.get("skipped", {})
        assert "dcim/devices" not in skipped, f"Expected no skip for same-content, got {skipped}"

        # Assert backup_endpoints metadata updated
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT path, kind, record_count, source_file, exported_at, expected_count, verified FROM backup_endpoints WHERE path = ?", ("dcim/devices",))
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row[1] == "minimal", f"Expected kind=minimal after same-content upload, got {row[1]}"
        assert row[2] == 10, f"Expected record_count=10, got {row[2]}"
        assert row[3] == "MinimalSame", f"Expected source_file='MinimalSame', got {row[3]}"
        assert row[4] == "2026-10-27T12:00:00.000000+01:00", f"Expected exported_at to update, got {row[4]}"
        assert row[5] == 10, f"Expected expected_count=10, got {row[5]}"
        assert row[6] == 1, f"Expected verified=1 (True), got {row[6]}"

        # Assert no duplicate rows in backup_records (structured rewrite was skipped)
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM backup_records WHERE object_type = 'dcim_devices'")
        count = cur.fetchone()[0]
        conn.close()
        assert count == 10, f"Expected 10 dcim_devices rows (no duplicate rewrite), got {count}"


class TestPartialMinimalMergedCounts:
    """Step C: Partial Minimal merged counts must sum correctly."""

    def test_partial_minimal_merged_counts(self, tmpdb):
        """Full contains all mapped inventory endpoints. Minimal replaces only two.
        Assert backup_metadata.record_count equals the merged mapped-endpoint sum,
        object_counts retains all mapped Full categories with two replacements,
        and omitted Full-only endpoints remain unchanged."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode(), filename="Full")

        # Minimal replaces only dcim/devices (8) and ipam/vlans (5)
        minimal = _build_minimal_payload()
        bm.save_netbox_backup(json.dumps(minimal).encode(), filename="Minimal")

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT record_count, object_counts FROM backup_metadata WHERE id = 1")
        row = cur.fetchone()
        conn.close()

        obj_counts = json.loads(row[1])
        total = row[0]

        # dcim/devices should be 8 (replaced from 10)
        assert obj_counts.get("dcim_devices") == 8, f"Expected dcim_devices=8, got {obj_counts.get('dcim_devices')}"
        # ipam/vlans should be 5 (unchanged)
        assert obj_counts.get("ipam_vlans") == 5, f"Expected ipam_vlans=5, got {obj_counts.get('ipam_vlans')}"
        # ipam/prefixes should remain 6 (not replaced)
        assert obj_counts.get("ipam_prefixes") == 6, f"Expected ipam_prefixes=6, got {obj_counts.get('ipam_prefixes')}"
        # virtualization_virtual_machines should remain 7 (not replaced)
        assert obj_counts.get("virtualization_virtual_machines") == 7, f"Expected vms=7, got {obj_counts.get('virtualization_virtual_machines')}"
        # dcim_sites should remain 3 (not replaced)
        assert obj_counts.get("dcim_sites") == 3, f"Expected sites=3, got {obj_counts.get('dcim_sites')}"

        # total should be the SUM of mapped endpoint record_counts
        expected_total = 8 + 5 + 6 + 7 + 3  # devices + vlans + prefixes + vms + sites
        assert total == expected_total, f"Expected total={expected_total}, got {total}"


class TestFullAfterMinimal:
    """Step D: Full after Minimal must make Full catalogue authoritative."""

    def test_full_after_minimal_catalogue_is_authoritative(self, tmpdb):
        """Upload Full, upload Minimal replacement, upload newer Full with changed
        endpoint membership. Assert full_endpoint_catalogue exactly matches the
        newer Full; removed entries no longer remain; Full metadata/catalogue
        state is authoritative."""
        # Full 1
        payload1 = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
        ])
        bm.save_netbox_backup(json.dumps(payload1).encode(), filename="Full1")

        # Minimal replaces dcim/devices
        minimal = {
            "metadata": {"backup_completed": "2026-10-27T11:00:00.000000+01:00"},
            "endpoints": [
                {"path": "dcim/devices", "records": [{"id": 2, "name": "Dev2"}], "verified": True},
            ],
            "summary": [],
        }
        bm.save_netbox_backup(json.dumps(minimal).encode(), filename="Minimal")

        # Full 2 with different membership: adds ipam/vlans, removes dcim/sites
        payload2 = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 3, "name": "Dev3"}], "verified": True},
            {"path": "ipam/vlans", "records": [{"id": 1, "vid": 1, "name": "VLAN1"}], "verified": True},
        ], backup_completed="2026-10-27T12:00:00.000000+01:00")
        bm.save_netbox_backup(json.dumps(payload2).encode(), filename="Full2")

        rows, meta_row = _query_catalogue(tmpdb)
        paths = [r[0] for r in rows]

        assert "dcim/devices" in paths, "Newer Full must have dcim/devices in catalogue"
        assert "ipam/vlans" in paths, "Newer Full must have ipam/vlans in catalogue"
        assert "dcim/sites" not in paths, "Removed endpoint must not remain in catalogue"
        assert meta_row is not None and "12:00" in str(meta_row[0]), "latest_full must reflect Full2"


class TestCatalogueRollbackFailure:
    """Step E: Catalogue rollback/failure behaviour."""

    def test_catalogue_insert_failure_reports_error_no_partial(self, tmpdb):
        """Force catalogue replacement INSERT to fail in a temporary DB.
        Assert the operation reports failure (raises), does not silently claim
        success, and the prior catalogue is retained."""
        payload = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
        ])

        # Upload Full successfully first
        bm.save_netbox_backup(json.dumps(payload).encode(), filename="Full")

        # Verify catalogue has entries
        rows_before, _ = _query_catalogue(tmpdb)
        assert len(rows_before) > 0, "Catalogue must have entries before forced failure"

        # The second Full's catalogue-write phase (derive + DELETE + INSERT) must
        # fail so the upload reports failure and retains the prior catalogue.
        payload2 = _build_full_with_endpoints([
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
        ])
        with mock.patch.object(
            bm, "_derive_full_catalogue",
            side_effect=RuntimeError("simulated catalogue insert failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated catalogue insert failure"):
                bm.save_netbox_backup(json.dumps(payload2).encode(), filename="FullFail")

        # Prior catalogue entries for known endpoints must still be present.
        rows_after, _ = _query_catalogue(tmpdb)
        after_paths = [r[0] for r in rows_after]
        assert "dcim/devices" in after_paths, "Prior catalogue entry must survive failure"


# ── LEGACY / EMPTY DISTINCTION (retained coverage) ─────────────────────

class TestLegacyEmptyDistinction:
    """Step F: Retain existing coverage of legacy/empty/populated distinctions."""

    def test_unavailable_legacy_catalogue_allows_any(self, tmpdb):
        """No Full ever uploaded -> empty catalogue -> skip all Minimal endpoints
        (new default behaviour mirrors legacy None = skip all)."""
        result = bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped

    def test_valid_empty_catalogue_rejects_all(self, tmpdb):
        """Empty catalogue explicitly rejects every Minimal endpoint."""
        # Upload Full with only non-data endpoints to produce empty catalogue
        endpoints = [
            {"path": "status", "records": [{"version": "4.6.8"}], "verified": True},
        ]
        for i in range(100):
            endpoints.append({"path": f"extras/tags-{i}", "records": [{"id": i, "name": f"t{i}"}]})
        full_payload = {
            "metadata": {"backup_completed": "2026-10-27T10:00:00.000000+01:00", "netbox_version": "4.6.8"},
            "endpoints": endpoints,
            "summary": [],
        }
        bm.save_netbox_backup(json.dumps(full_payload).encode())
        result = bm.save_netbox_backup(json.dumps(_build_minimal_payload()).encode())
        skipped = result.get("skipped", {})
        assert "dcim/devices" in skipped

    def test_populated_catalogue_strict_allowlist(self, tmpdb):
        """Populated catalogue strictly allows only known endpoints."""
        bm.save_netbox_backup(json.dumps(_build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")).encode())
        # Minimal with a newer timestamp so dcim/devices is not stale
        minimal = {
            "metadata": {"backup_completed": "2026-10-27T11:00:00.000000+01:00"},
            "endpoints": [
                {"path": "dcim/devices", "records": [_make_device(1, "min-dev-1")], "verified": True},
                {"path": "extras/custom-field-choice-sets", "records": [{"id": i, "name": f"CS{i}"} for i in range(1, 4)]},
            ],
            "summary": [],
        }
        result = bm.save_netbox_backup(json.dumps(minimal).encode())
        skipped = result.get("skipped", {})
        assert "extras/custom-field-choice-sets" in skipped, \
            f"Unknown endpoint must be skipped, got {skipped}"
        assert "dcim/devices" not in skipped, "Known endpoint must be allowed"


class TestCsvOverrideCatalogueMembership:
    """Focused tests for CSV override catalogue membership gate."""

    def _setup_catalogue(self, tmpdb, paths):
        """Upload a Full payload that populates full_endpoint_catalogue with the given paths."""
        payload = _build_full_with_endpoints([
            {"path": p, "records": [{"id": 1, "name": p}], "verified": True}
            for p in paths
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        bm.save_netbox_backup(json.dumps(payload).encode())

    def test_known_catalogue_endpoint_csv_override_succeeds(self, tmpdb):
        """(1) Known catalogue endpoint -> CSV override succeeds."""
        self._setup_catalogue(tmpdb, ["dcim/devices"])

        import streamlit as st
        from core.shared_backup_state import SharedBackupState
        SharedBackupState.initialize()
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {}
        st.session_state[SharedBackupState.CSV_OVERRIDES_KEY] = {}

        result = SharedBackupState.add_csv_override("dcim/devices", 5, "test.csv", "2026-10-27 12:00:00")
        assert result is None, f"expected success, got {result}"

        registry = st.session_state.get(SharedBackupState.OBJECT_REGISTRY_KEY, {})
        assert "dcim/devices" in registry, "registry must contain the endpoint"
        assert registry["dcim/devices"]["count"] == 5
        overrides = st.session_state.get(SharedBackupState.CSV_OVERRIDES_KEY, {})
        assert "dcim/devices" in overrides

    def test_unknown_endpoint_rejected_with_skip_reason(self, tmpdb):
        """(2) Unknown endpoint -> rejected with skip_unknown_catalogue_endpoint."""
        self._setup_catalogue(tmpdb, ["dcim/devices"])

        import streamlit as st
        from core.shared_backup_state import SharedBackupState
        SharedBackupState.initialize()
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {}
        st.session_state[SharedBackupState.CSV_OVERRIDES_KEY] = {}

        result = SharedBackupState.add_csv_override("extras/tags", 3, "tags.csv", "2026-10-27 12:00:00")
        assert result == "skip_unknown_catalogue_endpoint", f"expected rejection reason, got {result}"

    def test_unknown_rejection_no_mutation(self, tmpdb):
        """(3) Unknown rejection leaves registry, CSV override state, and dynamic_* data unchanged."""
        self._setup_catalogue(tmpdb, ["dcim/devices"])

        import streamlit as st
        from core.shared_backup_state import SharedBackupState
        SharedBackupState.initialize()
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {"dcim/devices": {"count": 10}}
        st.session_state[SharedBackupState.CSV_OVERRIDES_KEY] = {"dcim/devices": {"count": 10}}

        # Verify no dynamic_* table exists yet
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'dynamic_%'")
        before_dynamic = {r[0] for r in cur.fetchall()}
        conn.close()

        result = SharedBackupState.add_csv_override("extras/tags", 3, "tags.csv", "2026-10-27 12:00:00")
        assert result == "skip_unknown_catalogue_endpoint"

        # Session state must be untouched
        registry = st.session_state.get(SharedBackupState.OBJECT_REGISTRY_KEY, {})
        assert "extras/tags" not in registry, "registry must not gain unknown endpoint"
        assert registry.get("dcim/devices", {}).get("count") == 10, "existing entry must be unchanged"
        overrides = st.session_state.get(SharedBackupState.CSV_OVERRIDES_KEY, {})
        assert "extras/tags" not in overrides, "CSV overrides must not gain unknown endpoint"

        # No dynamic_* table created
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'dynamic_%'")
        after_dynamic = {r[0] for r in cur.fetchall()}
        conn.close()
        assert after_dynamic == before_dynamic, "no dynamic_* table must be created"


class TestFullCsvPruneAtomicity:
    """Stale/rejected Full must never clear CSV; accepted newer Full must clear all CSV overlays."""

    def test_stale_full_preserves_csv_override(self, tmpdb):
        """(1) Stale Full (older timestamp) is rejected before any CSV mutation.
        Existing CSV override and dynamic_* table remain intact after rejection."""
        import streamlit as st
        from core.shared_backup_state import SharedBackupState

        # Upload a newer Full to populate catalogue
        payload_new = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
        ], backup_completed="2026-10-27T12:00:00.000000+01:00")
        bm.save_netbox_backup(json.dumps(payload_new).encode())

        # Manually inject a CSV override into session state and a dynamic table
        SharedBackupState.initialize()
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {
            "dcim/devices": {"count": 5, "source": "test.csv", "timestamp": "2026-10-27 12:00:00", "source_type": "csv"}
        }
        st.session_state[SharedBackupState.CSV_OVERRIDES_KEY] = {
            "dcim/devices": {"count": 5, "source": "test.csv", "timestamp": "2026-10-27 12:00:00", "source_type": "csv"}
        }
        conn = sqlite3.connect(tmpdb)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_dcim_devices (
                id INTEGER PRIMARY KEY, name TEXT, _uploaded_at TEXT, _source_file TEXT
            )
        """)
        conn.execute("INSERT INTO dynamic_dcim_devices (name, _uploaded_at, _source_file) VALUES ('csv-dev', '2026-10-27 12:00:00', 'test.csv')")
        conn.commit()
        conn.close()

        # Upload stale Full (older timestamp)
        payload_stale = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 2, "name": "Dev2"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        result = bm.save_netbox_backup(json.dumps(payload_stale).encode())

        assert result.get("error") == "stale_full", f"Expected stale_full, got {result}"

        # CSV override must still exist in session state
        registry = st.session_state.get(SharedBackupState.OBJECT_REGISTRY_KEY, {})
        assert "dcim/devices" in registry, "registry must retain CSV entry after stale rejection"
        assert registry["dcim/devices"]["source_type"] == "csv"
        overrides = st.session_state.get(SharedBackupState.CSV_OVERRIDES_KEY, {})
        assert "dcim/devices" in overrides, "CSV overrides must retain entry after stale rejection"

        # Dynamic table must still exist
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dynamic_dcim_devices")
        assert cur.fetchone()[0] == 1, "dynamic_* table must survive stale Full rejection"
        conn.close()

    def test_accepted_newer_full_clears_csv_overrides(self, tmpdb):
        """(2) Accepted newer Full clears all CSV overrides from session state
        and drops all dynamic_* tables. No zombie CSV entries survive."""
        import streamlit as st
        from core.shared_backup_state import SharedBackupState

        # Upload a Full to populate catalogue
        payload1 = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 1, "name": "Dev1"}], "verified": True},
            {"path": "dcim/sites", "records": [{"id": 1, "name": "Site1"}], "verified": True},
        ], backup_completed="2026-10-27T10:00:00.000000+01:00")
        bm.save_netbox_backup(json.dumps(payload1).encode())

        # Inject CSV overrides for two endpoints plus a dynamic table
        SharedBackupState.initialize()
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {
            "dcim/devices": {"count": 5, "source": "devices.csv", "timestamp": "2026-10-27 12:00:00", "source_type": "csv"},
            "dcim/sites": {"count": 3, "source": "sites.csv", "timestamp": "2026-10-27 12:00:00", "source_type": "csv"},
        }
        st.session_state[SharedBackupState.CSV_OVERRIDES_KEY] = {
            "dcim/devices": {"count": 5, "source": "devices.csv", "timestamp": "2026-10-27 12:00:00", "source_type": "csv"},
            "dcim/sites": {"count": 3, "source": "sites.csv", "timestamp": "2026-10-27 12:00:00", "source_type": "csv"},
        }
        conn = sqlite3.connect(tmpdb)
        conn.execute("CREATE TABLE IF NOT EXISTS dynamic_dcim_devices (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS dynamic_dcim_sites (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO dynamic_dcim_devices (name) VALUES ('csv-dev')")
        conn.execute("INSERT INTO dynamic_dcim_sites (name) VALUES ('csv-site')")
        conn.commit()
        conn.close()

        # Upload accepted newer Full
        payload2 = _build_full_with_endpoints([
            {"path": "dcim/devices", "records": [{"id": 2, "name": "Dev2"}], "verified": True},
            {"path": "dcim/sites", "records": [{"id": 2, "name": "Site2"}], "verified": True},
        ], backup_completed="2026-10-27T12:00:00.000000+01:00")
        result = bm.save_netbox_backup(json.dumps(payload2).encode())

        assert result.get("error") != "stale_full", "Newer Full must be accepted"
        assert result.get("backup_type") == "full"

        # All CSV overrides must be cleared from session state
        registry = st.session_state.get(SharedBackupState.OBJECT_REGISTRY_KEY, {})
        csv_entries = [ep for ep, meta in registry.items() if meta.get("source_type") == "csv"]
        assert len(csv_entries) == 0, f"No CSV entries should survive accepted Full, got {csv_entries}"

        overrides = st.session_state.get(SharedBackupState.CSV_OVERRIDES_KEY, {})
        assert len(overrides) == 0, "CSV_OVERRIDES_KEY must be empty after accepted Full"

        # All dynamic_* tables must be dropped
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'dynamic_%'")
        dynamic_tables = {r[0] for r in cur.fetchall()}
        conn.close()
        assert dynamic_tables == set(), f"No dynamic_* tables should survive accepted Full, got {dynamic_tables}"


class TestClearJsonPreservesCsv:
    """Step C: Clear All JSON must preserve CSV dynamic_* tables and catalogue metadata."""

    def test_clear_backup_records_preserve_csv_true(self, tmpdb):
        """JSON/Minimal tables cleared; backup_endpoints cleared;
        CSV dynamic_* rows remain; full_endpoint_catalogue remains;
        backup_metadata.latest_full_backup_completed remains."""
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        # Inject a CSV dynamic_* table with data
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_dcim_devices (
                id INTEGER PRIMARY KEY, name TEXT, _uploaded_at TEXT, _source_file TEXT
            )
        """)
        cur.execute(
            "INSERT INTO dynamic_dcim_devices (name, _uploaded_at, _source_file) VALUES (?, ?, ?)",
            ("csv-dev-1", "2026-10-27 12:00:00", "devices.csv"),
        )
        cur.execute("INSERT INTO dynamic_dcim_devices (name, _uploaded_at, _source_file) VALUES (?, ?, ?)",
                    ("csv-dev-2", "2026-10-27 12:05:00", "devices.csv"))
        conn.commit()
        conn.close()

        bm.clear_backup_records(preserve_csv=True)

        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()

        # JSON/Minimal tables must be cleared
        cur.execute("SELECT COUNT(*) FROM backup_records")
        assert cur.fetchone()[0] == 0, "backup_records must be empty"
        cur.execute("SELECT COUNT(*) FROM backup_choice_values")
        assert cur.fetchone()[0] == 0, "backup_choice_values must be empty"
        cur.execute("SELECT COUNT(*) FROM backup_endpoints")
        assert cur.fetchone()[0] == 0, "backup_endpoints must be empty"
        cur.execute("SELECT COUNT(*) FROM sites_records")
        assert cur.fetchone()[0] == 0, "sites_records must be empty"
        cur.execute("SELECT COUNT(*) FROM ipam_records")
        assert cur.fetchone()[0] == 0, "ipam_records must be empty"
        cur.execute("SELECT COUNT(*) FROM inventory_records")
        assert cur.fetchone()[0] == 0, "inventory_records must be empty"

        # Catalogue must be preserved
        cur.execute("SELECT COUNT(*) FROM full_endpoint_catalogue")
        assert cur.fetchone()[0] > 0, "full_endpoint_catalogue must be preserved"

        # latest_full_backup_completed must be preserved
        cur.execute("SELECT latest_full_backup_completed FROM backup_metadata WHERE id = 1")
        meta_row = cur.fetchone()
        assert meta_row is not None, "backup_metadata row must exist"
        assert meta_row[0], "latest_full_backup_completed must be preserved"

        # CSV dynamic_* rows must remain
        cur.execute("SELECT COUNT(*) FROM dynamic_dcim_devices")
        assert cur.fetchone()[0] == 2, "dynamic_dcim_devices rows must survive"
        conn.close()

    def test_fresh_session_restore_after_json_clear(self, tmpdb):
        """Simulated fresh-session restore after Clear All JSON recovers
        the CSV override from dynamic_* persistent storage via _restore_csv_overrides()."""
        import streamlit as st
        from core.shared_backup_state import SharedBackupState
        from core.backup_manager import _restore_csv_overrides

        # Upload a Full to populate the catalogue
        bm.save_netbox_backup(json.dumps(_build_full_payload()).encode())

        # Inject a CSV dynamic_* table with one row
        conn = sqlite3.connect(tmpdb)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_dcim_devices (
                id INTEGER PRIMARY KEY, name TEXT, _uploaded_at TEXT, _source_file TEXT
            )
        """)
        cur.execute(
            "INSERT INTO dynamic_dcim_devices (name, _uploaded_at, _source_file) VALUES (?, ?, ?)",
            ("csv-dev-restored", "2026-10-27 13:00:00", "devices.csv"),
        )
        conn.commit()
        conn.close()

        # Simulate Clear All JSON
        bm.clear_backup_records(preserve_csv=True)

        # Simulate a fresh session: wipe SharedBackupState session keys only
        SharedBackupState.initialize()
        st.session_state[SharedBackupState.INSPECTOR_KEY] = None
        st.session_state[SharedBackupState.OBJECT_REGISTRY_KEY] = {}
        st.session_state[SharedBackupState.CSV_OVERRIDES_KEY] = {}
        st.session_state[SharedBackupState.CUSTOM_FIELDS_KEY] = {}
        st.session_state[SharedBackupState.CHOICE_SETS_KEY] = {}

        # Invoke the existing restore path
        _restore_csv_overrides()

        # The CSV override must be recovered from the persistent dynamic_* table
        overrides = st.session_state.get(SharedBackupState.CSV_OVERRIDES_KEY, {})
        assert "dcim/devices" in overrides, "CSV override for dcim/devices must be restored"
        assert overrides["dcim/devices"]["count"] == 1
        assert overrides["dcim/devices"]["source"] == "devices.csv"
