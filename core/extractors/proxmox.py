import re
from typing import List, Dict, Any, Optional
from .base import BaseTopologyExtractor, get_bounding_box


class ProxmoxTopologyExtractor(BaseTopologyExtractor):
    """
    Extractor for Proxmox VE Web UI (Network Table / DataGrid view).
    Extracts Linux Bridge (vmbr), Bond (bond), and physical NICs (eno, ens, eth).
    """
    def extract(
        self,
        raw_tokens: List[Dict[str, Any]],
        image_width: int = 1920,
        image_height: int = 1080,
        plat_cfg: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not raw_tokens:
            return []

        # Proxmox VE network GUI is a tabular layout.
        # Group tokens into lines by vertical delta <= 8px.
        lines = []
        sorted_tokens = sorted(raw_tokens, key=lambda t: get_bounding_box(t)[1])
        curr_line = []
        curr_y = None

        for t in sorted_tokens:
            _, y0, _, _ = get_bounding_box(t)
            if curr_y is None or abs(y0 - curr_y) <= 8.0:
                curr_line.append(t)
                curr_y = y0
            else:
                lines.append(sorted(curr_line, key=lambda x: get_bounding_box(x)[0]))
                curr_line = [t]
                curr_y = y0
        if curr_line:
            lines.append(sorted(curr_line, key=lambda x: get_bounding_box(x)[0]))

        extracted_records = []
        for line in lines:
            line_str = " ".join(str(t.get("text", "")).strip() for t in line)

            # Match Linux Bridge: vmbr<N>
            m_br = re.search(r"\b(vmbr\d+)\b", line_str, re.I)
            if m_br:
                br_name = m_br.group(1).lower()
                # Check for IP/CIDR in line
                m_ip = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?)\b", line_str)
                cidr_ip = m_ip.group(1) if m_ip else ""

                # Check for bridge ports / slaves
                ports = re.findall(r"\b(eno\d+|ens\d+|eth\d+|bond\d+)\b", line_str, re.I)
                slaves = [p.lower() for p in ports if p.lower() != br_name]

                if slaves:
                    for sl in slaves:
                        extracted_records.append({
                            "switch": br_name,
                            "interface": sl,
                            "type": "Uplink",
                            "ip": "",
                            "purpose": "",
                            "role": "Active Uplink",
                            "speed": ""
                        })
                else:
                    extracted_records.append({
                        "switch": br_name,
                        "interface": "No Uplink",
                        "type": "Uplink",
                        "ip": "",
                        "purpose": "",
                        "role": "None",
                        "speed": ""
                    })

                if cidr_ip:
                    extracted_records.append({
                        "switch": br_name,
                        "interface": br_name,
                        "type": "Bridge",
                        "ip": cidr_ip,
                        "purpose": "Management Network",
                        "role": "None",
                        "speed": ""
                    })

        return extracted_records
