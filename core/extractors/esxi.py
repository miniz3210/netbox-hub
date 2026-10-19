import re
from typing import List, Dict, Any, Optional
from .base import BaseTopologyExtractor, get_bounding_box


def apply_normalizations(text: str, normalizations: List[Dict[str, str]]) -> str:
    s = text
    for rule in normalizations:
        pat = rule.get("pattern")
        rep = rule.get("replace", "")
        if pat:
            s = re.sub(pat, rep, s)
    return s.strip()


class EsxiTopologyExtractor(BaseTopologyExtractor):
    def extract(
        self,
        raw_tokens: List[Dict[str, Any]],
        image_width: int = 1920,
        image_height: int = 1080,
        plat_cfg: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not raw_tokens:
            return []

        cfg = plat_cfg or {}
        normalizations = cfg.get("token_normalizations", [])
        noise_filters = cfg.get("noise_filters", [])
        if noise_filters:
            escaped = [re.escape(n.strip()) for n in noise_filters if n and n.strip()]
            noise_re = re.compile(r"^(?:" + r"|".join(escaped) + r")$|(?:" + r"|".join(escaped) + r")", re.I)
        else:
            noise_re = None

        # Step 1: Detect Switch Headers & Dynamic Sidebar X-Cutoff
        switch_headers = []
        for t in raw_tokens:
            text = str(t.get("text", "")).strip()
            m = re.search(r"^(?:>|▶|v|▼|\+|-)?\s*(?:Standard\s*Switch|Distributed\s*Switch|Bridge):\s*([^\n\r]+)", text, re.I)
            if m:
                bx, by, _, _ = get_bounding_box(t)
                raw_sw_name = m.group(1).strip()
                raw_sw_name = re.split(r"\s+(?:ADD|EDIT|MANAGE|\.\.\.)", raw_sw_name, flags=re.I)[0].strip()
                sw_name = apply_normalizations(raw_sw_name, normalizations)

                is_collapsed = bool(re.match(r"^(?:>|▶|\+)", text.strip()))
                switch_headers.append({
                    "name": sw_name,
                    "y_top": by,
                    "x_left": bx,
                    "collapsed": is_collapsed
                })

        switch_headers.sort(key=lambda s: s["y_top"])
        if not switch_headers:
            return []

        min_switch_x = min(sh["x_left"] for sh in switch_headers)
        sidebar_cutoff_x = max(0.0, min_switch_x - 15.0)

        for idx, sh in enumerate(switch_headers):
            sh["y_start"] = sh["y_top"]
            if idx + 1 < len(switch_headers):
                sh["y_end"] = switch_headers[idx + 1]["y_top"]
            else:
                sh["y_end"] = float(image_height)

        # Step 2: Extract Records per Switch Band
        extracted_records: List[Dict[str, Any]] = []

        for sh in switch_headers:
            sw_name = sh["name"]

            inner_tokens = []
            for t in raw_tokens:
                bx0, by0, bx1, by1 = get_bounding_box(t)
                text = str(t.get("text", "")).strip()

                if bx0 < sidebar_cutoff_x:
                    continue
                if not (sh["y_start"] + 25.0 <= by0 < sh["y_end"]):
                    continue

                if noise_re and noise_re.search(text):
                    continue
                if re.match(r"^(?:ADD\s*NETWORKING|REFRESH|EDIT|MANAGE\s*PHYSICAL|ACTIONS|\.\.\.)$", text, re.I):
                    continue
                if re.search(r"(?:Standard|Distributed)\s*Switch:", text, re.I):
                    continue
                if text.startswith(">") or text.startswith("▶"):
                    continue

                inner_tokens.append(t)

            if not inner_tokens:
                extracted_records.append({
                    "switch": sw_name,
                    "interface": "No Uplink",
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "None",
                    "speed": ""
                })
                continue

            # Dynamic Physical Column Anchor Detection
            phys_anchor_x = None
            for t in inner_tokens:
                txt = t.get("text", "").strip()
                if re.search(r"(?:Physical\s*Adapters|.*-DVUplinks-.*|\bvmnic\d+\b|No\s*physical\s*network\s*adapters)", txt, re.I):
                    x0, _, _, _ = get_bounding_box(t)
                    if phys_anchor_x is None or x0 < phys_anchor_x:
                        phys_anchor_x = x0

            if phys_anchor_x is not None and phys_anchor_x > (sidebar_cutoff_x + 100.0):
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < (phys_anchor_x - 10.0)]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= (phys_anchor_x - 15.0)]
            else:
                x_min_band = min(get_bounding_box(t)[0] for t in inner_tokens)
                x_max_band = max(get_bounding_box(t)[2] for t in inner_tokens)
                x_mid_band = x_min_band + (x_max_band - x_min_band) * 0.48
                logical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] < x_mid_band]
                physical_tokens = [t for t in inner_tokens if get_bounding_box(t)[0] >= x_mid_band]

            # Physical Column Extraction
            physical_records = []
            vmnic_matches = []
            for pt in physical_tokens:
                txt = pt.get("text", "").strip()
                if re.search(r"\(0\s*NIC\s*Adapters\)", txt, re.I):
                    continue
                m = re.search(r"(?:[xX!\*\-\+]\s*)?\b(vmnic\d+|eno\d+|ens\d+|eth\d+|bond\d+)\b(?:\s*(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000))?", txt, re.I)
                if m:
                    nic_id = m.group(1).lower()
                    speed_str = m.group(2) or ""
                    is_standby = bool(re.search(r"(?:^[xX!]\s*|Standby)", txt, re.I))
                    vmnic_matches.append({
                        "nic": nic_id,
                        "speed": speed_str,
                        "standby": is_standby,
                        "y": get_bounding_box(pt)[1]
                    })

            seen_nics = set()
            for vm in sorted(vmnic_matches, key=lambda x: x["y"]):
                if vm["nic"] in seen_nics:
                    continue
                seen_nics.add(vm["nic"])

                nic_speed = vm["speed"]
                if not nic_speed:
                    for pt in physical_tokens:
                        if abs(get_bounding_box(pt)[1] - vm["y"]) < 18.0:
                            sm = re.search(r"(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)|10000|1000)", pt.get("text", ""), re.I)
                            if sm:
                                nic_speed = sm.group(1).strip()
                                break

                physical_records.append({
                    "switch": sw_name,
                    "interface": vm["nic"],
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "Standby Uplink" if vm["standby"] else "Active Uplink",
                    "speed": nic_speed
                })

            if not physical_records:
                physical_records.append({
                    "switch": sw_name,
                    "interface": "No Uplink",
                    "type": "Uplink",
                    "ip": "",
                    "purpose": "",
                    "role": "None",
                    "speed": ""
                })
            extracted_records.extend(physical_records)

            # Logical Column Extraction
            vmk_entries = []
            for lt in logical_tokens:
                txt = lt.get("text", "").strip()
                m_vmk = re.search(r"\b(vmk\d+)\s*[:\s]\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt, re.I)
                if m_vmk:
                    vmk_entries.append({
                        "vmk": m_vmk.group(1).lower(),
                        "ip": m_vmk.group(2).strip(),
                        "y": get_bounding_box(lt)[1]
                    })
                else:
                    m_ip = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", txt)
                    if m_ip:
                        ip_val = m_ip.group(1).strip()
                        y_ip = get_bounding_box(lt)[1]
                        found_vmk = None
                        for lt2 in logical_tokens:
                            if abs(get_bounding_box(lt2)[1] - y_ip) < 18.0:
                                m_v = re.search(r"\b(vmk\d+)\b", lt2.get("text", ""), re.I)
                                if m_v:
                                    found_vmk = m_v.group(1).lower()
                                    break
                        if found_vmk:
                            vmk_entries.append({
                                "vmk": found_vmk,
                                "ip": ip_val,
                                "y": y_ip
                            })

            service_titles = []
            for lt in logical_tokens:
                txt = lt.get("text", "").strip()
                if re.match(r"^(?:[vV✓✔\-\s]*(?:VLAN\s*ID|VMkernel\s*Ports|Virtual\s*Machines)|vmk\d+|No\s*associated)", txt, re.I):
                    continue
                if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", txt):
                    continue
                if re.search(r"(?:Standard|Distributed)\s*Switch:", txt, re.I):
                    continue
                if noise_re and noise_re.search(txt):
                    continue
                if len(txt) <= 2:
                    continue

                cleaned_title = apply_normalizations(txt, normalizations)
                service_titles.append({
                    "title": cleaned_title,
                    "y": get_bounding_box(lt)[1]
                })

            service_titles.sort(key=lambda s: s["y"])

            assigned_titles = set()
            for v in vmk_entries:
                candidates = [st for st in service_titles if 0 <= (v["y"] - st["y"]) <= 140.0]
                if candidates:
                    candidates.sort(key=lambda c: v["y"] - c["y"])
                    purpose_name = candidates[0]["title"]
                    assigned_titles.add(purpose_name)
                else:
                    purpose_name = "Management Network" if v["vmk"] == "vmk0" else v["vmk"]

                extracted_records.append({
                    "switch": sw_name,
                    "interface": v["vmk"],
                    "type": "VMkernel",
                    "ip": v["ip"],
                    "purpose": purpose_name,
                    "role": "None",
                    "speed": ""
                })

            for st_item in service_titles:
                title = st_item["title"]
                if title in assigned_titles:
                    continue
                assigned_titles.add(title)
                extracted_records.append({
                    "switch": sw_name,
                    "interface": "",
                    "type": "Port Group",
                    "ip": "",
                    "purpose": title,
                    "role": "None",
                    "speed": ""
                })

        return extracted_records
