import os
import sys
import json
import logging
import subprocess
import tornado.ioloop
import tornado.web
import tornado.httpclient
import tornado.websocket
from core.db_manager import save_sites_batch, save_ipam_records_batch, save_records_batch, init_db
from config.constants import APP_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("netbox-hub-gateway")

init_db()

HUB_SECRET_KEY = os.getenv("HUB_SYNC_KEY", "netbox-hub-secret-sync-key")
STREAMLIT_PORT = 8502
GATEWAY_PORT = int(os.getenv("PORT", 8501))

class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write({"status": "online", "version": APP_VERSION})

class SyncPushHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "*")
        self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def options(self):
        self.set_status(204)
        self.finish()

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write({"status": "online", "endpoint": "/api/v1/sync/push"})

    def post(self):
        self.set_header("Content-Type", "application/json")
        try:
            auth_header = self.request.headers.get("X-Hub-Key", "")
            body = self.request.body.decode('utf-8')
            payload = json.loads(body) if body else {}

            if HUB_SECRET_KEY and auth_header != HUB_SECRET_KEY and payload.get("sync_key") != HUB_SECRET_KEY:
                self.set_status(401)
                self.write({"success": False, "error": "Unauthorized: Invalid X-Hub-Key"})
                return

            sites_data = payload.get("sites") or payload.get("dcim_sites") or []
            vlans_data = payload.get("vlans") or payload.get("ipam_vlans") or []
            prefixes_data = payload.get("prefixes") or payload.get("ipam_prefixes") or []
            devices_data = payload.get("devices") or payload.get("dcim_devices") or []
            vms_data = payload.get("vms") or payload.get("virtualization_virtual_machines") or []

            imported = {"sites": 0, "prefixes": 0, "devices": 0, "vms": 0}

            # 1. Sites
            if sites_data:
                site_records = []
                for s in sites_data:
                    s_id = s.get("id")
                    s_name = s.get("name")
                    s_slug = s.get("slug")
                    if s_id and s_name:
                        site_records.append({"id": s_id, "name": s_name, "slug": s_slug})
                if site_records:
                    imported["sites"] = save_sites_batch(site_records, clear_first=True)

            # 2. IPAM (VLANs & Prefixes)
            ipam_records = []
            for v in vlans_data:
                vid = v.get("vid")
                vname = v.get("name", "")
                role_val = v.get("role")
                role_str = role_val.get("name", "") if isinstance(role_val, dict) else str(role_val or "")
                site_val = v.get("site")
                site_str = site_val.get("name", "") if isinstance(site_val, dict) else str(site_val or "")
                desc = v.get("description", "")
                for p in (v.get("prefixes", []) or []):
                    pfx_str = p.get("prefix", "") if isinstance(p, dict) else str(p)
                    if pfx_str and "/" in pfx_str:
                        ipam_records.append({
                            "prefix_or_subnet": pfx_str,
                            "vlan_id": vid,
                            "vlan_name": vname,
                            "role": role_str,
                            "site": site_str,
                            "description": desc
                        })

            for p in prefixes_data:
                pfx_str = p.get("prefix", "")
                vlan_obj = p.get("vlan") or {}
                vid = vlan_obj.get("vid")
                vname = vlan_obj.get("name", "")
                role_val = p.get("role") or {}
                role_str = role_val.get("name", "") if isinstance(role_val, dict) else str(role_val or "")
                site_val = p.get("site") or {}
                site_str = site_val.get("name", "") if isinstance(site_val, dict) else str(site_val or "")
                desc = p.get("description", "")
                if pfx_str and "/" in pfx_str:
                    ipam_records.append({
                        "prefix_or_subnet": pfx_str,
                        "vlan_id": vid,
                        "vlan_name": vname,
                        "role": role_str,
                        "site": site_str,
                        "description": desc
                    })

            if ipam_records:
                imported["prefixes"] = save_ipam_records_batch(ipam_records, clear_first=True)

            # 3. Inventory (Devices & VMs)
            inv_records = []
            for d in devices_data:
                name = d.get("name") or ""
                if not name:
                    continue
                dtype = (d.get("device_type") or {}).get("model", "")
                mfg = ((d.get("device_type") or {}).get("manufacturer") or {}).get("name", "")
                role = (d.get("role") or d.get("device_role") or {}).get("name", "")
                site = (d.get("site") or {}).get("name", "")
                cluster = (d.get("cluster") or {}).get("name", "")
                desc = d.get("description") or ""

                combined = f"{name} {role} {dtype} {desc}".lower()
                cat = "hypervisor" if any(h in combined for h in ["esx", "hypervisor", "infhost", "vmhost", "esxi"]) else "device"

                inv_records.append({
                    "category": cat,
                    "name": name,
                    "description": desc,
                    "manufacturer": mfg,
                    "model_or_role": dtype or role,
                    "site": site,
                    "cluster": cluster
                })

            for vm in vms_data:
                name = vm.get("name") or ""
                if not name:
                    continue
                role = (vm.get("role") or {}).get("name", "")
                site = (vm.get("site") or {}).get("name", "")
                cluster = (vm.get("cluster") or {}).get("name", "")
                desc = vm.get("description") or ""

                inv_records.append({
                    "category": "vm",
                    "name": name,
                    "description": desc,
                    "manufacturer": "Virtual Machine",
                    "model_or_role": role or "VM",
                    "site": site,
                    "cluster": cluster
                })

            if inv_records:
                counts = save_records_batch(inv_records, clear_first=True)
                imported["devices"] = counts.get("device", 0) + counts.get("hypervisor", 0)
                imported["vms"] = counts.get("vm", 0)

            self.write({"success": True, "imported": imported})
        except Exception as e:
            logger.exception("Push processing failed")
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class StreamlitProxyHandler(tornado.web.RequestHandler):
    async def get(self, url):
        await self._forward("GET")

    async def post(self, url):
        await self._forward("POST")

    async def put(self, url):
        await self._forward("PUT")

    async def delete(self, url):
        await self._forward("DELETE")

    async def options(self, url):
        await self._forward("OPTIONS")

    async def _forward(self, method):
        target_url = f"http://127.0.0.1:{STREAMLIT_PORT}/{self.request.uri.lstrip('/')}"
        http_client = tornado.httpclient.AsyncHTTPClient()
        try:
            req = tornado.httpclient.HTTPRequest(
                url=target_url,
                method=method,
                headers=self.request.headers,
                body=self.request.body if method in ["POST", "PUT"] else None,
                follow_redirects=False,
                request_timeout=120.0
            )
            response = await http_client.fetch(req, raise_error=False)
            self.set_status(response.code)
            for header, v in response.headers.get_all():
                if header.lower() not in ["transfer-encoding", "content-encoding", "content-length"]:
                    self.set_header(header, v)
            if response.body:
                self.write(response.body)
        except Exception as e:
            self.set_status(502)
            self.write(f"Streamlit Proxy Error: {e}")

class StreamlitWSProxyHandler(tornado.websocket.WebSocketHandler):
    def open(self, *args, **kwargs):
        ws_url = f"ws://127.0.0.1:{STREAMLIT_PORT}/{self.request.uri.lstrip('/')}"
        tornado.websocket.websocket_connect(
            ws_url,
            callback=self._on_upstream_connected,
            on_message_callback=self._on_upstream_message
        )

    def _on_upstream_connected(self, future):
        try:
            self.upstream = future.result()
        except Exception as e:
            logger.error(f"WebSocket upstream connect failed: {e}")
            self.close()

    def _on_upstream_message(self, message):
        if message is not None:
            self.write_message(message, binary=isinstance(message, bytes))
        else:
            self.close()

    def on_message(self, message):
        if hasattr(self, 'upstream') and self.upstream:
            self.upstream.write_message(message, binary=isinstance(message, bytes))

    def on_close(self):
        if hasattr(self, 'upstream') and self.upstream:
            self.upstream.close()

def main():
    # 1. Start Streamlit in background on port 8502
    cmd = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.port", str(STREAMLIT_PORT),
        "--server.address", "127.0.0.1",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false"
    ]
    logger.info(f"Starting Streamlit backend on 127.0.0.1:{STREAMLIT_PORT}...")
    st_proc = subprocess.Popen(cmd)

    # 2. Build Gateway Application on port 8501
    app = tornado.web.Application(
        [
            (r"/api/v1/health/?", HealthHandler),
            (r"/api/v1/sync/push/?", SyncPushHandler),
            (r"/_stcore/stream", StreamlitWSProxyHandler),
            (r"/(.*)", StreamlitProxyHandler),
        ],
        max_buffer_size=100 * 1024 * 1024,
    )

    logger.info(f"NetBox Hub Gateway listening on 0.0.0.0:{GATEWAY_PORT}...")
    app.listen(GATEWAY_PORT, max_buffer_size=100 * 1024 * 1024)

    try:
        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        st_proc.terminate()

if __name__ == "__main__":
    main()