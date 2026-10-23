"""
vSphere Client 'Virtual switches' page text -> topology records (no OCR).
Input : text from copy(document.body.innerText) on Host > Configure > Networking > Virtual switches.
Output: (records, warnings); records = 7-key contract + 'vlan'.
A port-group title is the line directly above a 'VLAN ID:' line, so VM names, 'No associated port groups',
buttons etc. are never mistaken for port groups.
"""
import re
from typing import Any, Dict, List, Tuple

_SW_HDR  = re.compile(r"^\s*(Distributed|Standard)\s+Switch:\s*(.+?)\s*$", re.I)
_VLAN    = re.compile(r"^\s*VLAN\s*ID:\s*(\S+)\s*$", re.I)
_VMK     = re.compile(r"^\s*(vmk\d+)\s*:?\s*(\d{1,3}(?:\.\d{1,3}){3})?\s*$", re.I)
_VMNIC   = re.compile(r"^\s*(vmnic\d+)\b(.*)$", re.I)
_SLOT    = re.compile(r"^\s*uplink\s*\d+\s*\((\d+)\s*NIC\s*Adapters?\)", re.I)
_SPEED   = re.compile(r"(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)(?:\s*,?\s*(?:Full|Half))?)", re.I)
_NOADAPT = re.compile(r"^\s*No physical network adapters\s*$", re.I)
_NOPG    = re.compile(r"^\s*No associated port groups\s*$", re.I)
_IGNORE  = re.compile(r"^\s*(MANAGE PHYSICAL ADAPTERS|ADD NETWORKING.*|REFRESH|EDIT|VMkernel Ports\s*\(\d+\)|"
                      r"Virtual Machines\s*\(\d+\)|Physical Adapters|.*-DVUplinks.*)\s*$", re.I)

def _slice_page(lines: List[str]) -> List[str]:
    start = 0
    for i, l in enumerate(lines[:-1]):
        nxt = lines[i + 1].strip()
        if l.strip().lower() == "virtual switches" and (nxt.upper().startswith(("ADD NETWORKING", "REFRESH")) or _SW_HDR.match(nxt)):
            start = i + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].strip().lower() == "recent tasks":
            end = i
            break
    return lines[start:end]

def parse_vsphere_text(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
    lines = _slice_page(lines)
    recs: List[Dict[str, Any]] = []
    warns: List[str] = []
    sw = None
    st: Dict[str, Any] = {}

    def new_rec(**kw):
        r = {"switch": sw, "interface": "", "type": "", "ip": "", "purpose": "", "role": "None", "speed": "", "vlan": ""}
        r.update(kw)
        recs.append(r)
        return r

    def close():
        if sw is None:
            return
        if not st["content"]:
            warns.append(f"{sw}: collapsed or empty in text - expand it in vSphere and copy again")
        elif st["slots"] > st["nics"]:
            warns.append(f"{sw}: uplink slots list {st['slots']} NIC(s) but only {st['nics']} vmnic name(s) in text (expand the uplinks)")
        if st["noadapt"] and st["nics"] == 0:
            new_rec(interface="No Uplink", type="Uplink")
        for r in recs:
            if r["switch"] == sw and r["type"] == "VMkernel" and not r["ip"]:
                warns.append(f"{sw}: {r['interface']} ({r['purpose']}) has no IP in text")

    last_pg = None
    for i, line in enumerate(lines):
        m = _SW_HDR.match(line)
        if m:
            close()
            sw = m.group(2).strip()
            st = {"content": False, "slots": 0, "nics": 0, "noadapt": False}
            last_pg = None
            continue
        if sw is None:
            continue
        if _NOADAPT.match(line):
            st["content"] = True
            st["noadapt"] = True
            continue
        if _NOPG.match(line):
            st["content"] = True
            continue
        ms = _SLOT.match(line)
        if ms:
            st["content"] = True
            st["slots"] += int(ms.group(1))
            continue
        if _IGNORE.match(line) or _VLAN.match(line):
            continue
        mn = _VMNIC.match(line)
        if mn:
            sp = _SPEED.search(mn.group(2) or "")
            new_rec(interface=mn.group(1).lower(), type="Uplink", role="Active Uplink", speed=sp.group(1).strip() if sp else "")
            st["content"] = True
            st["nics"] += 1
            continue
        mk = _VMK.match(line)
        if mk:
            target = last_pg if last_pg is not None else new_rec(type="Port Group", purpose=mk.group(1).lower())
            target.update(type="VMkernel", interface=mk.group(1).lower(), ip=mk.group(2) or "")
            st["content"] = True
            continue
        if i + 1 < len(lines) and _VLAN.match(lines[i + 1]):
            v = _VLAN.match(lines[i + 1]).group(1)
            last_pg = new_rec(type="Port Group", purpose=line, vlan="" if set(v) <= {"-"} else v)
            st["content"] = True
            continue

    close()
    return recs, warns
