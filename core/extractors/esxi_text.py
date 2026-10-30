"""
vSphere Client 'Virtual switches' page text -> topology records (no OCR), with gap detection.

Input : text from copy(document.body.innerText) on Host > Configure > Networking > Virtual switches.
API   : parse_vsphere_text(text)    -> (records, warnings)                    # backward compatible
        parse_vsphere_text_ex(text) -> {"records", "warnings", "gaps"}
Records: 7-key contract + 'vlan'.

A port-group title is the line directly above a 'VLAN ID:' line, so VM names, 'No associated port groups',
buttons etc. are never mistaken for port groups.

A *gap* is something the PAGE ITSELF says should exist but the pasted text lacks:
  'VMkernel Ports (n)' with fewer than n 'vmkX : ip' lines  -> kind 'vmk_missing'   (OCR can help)
  'uplinkN (k NIC Adapters)' slots with fewer vmnic lines    -> kind 'nic_missing'   (OCR can help)
  a switch header with nothing under it                      -> kind 'collapsed'     (OCR cannot help: expand and copy again)
"""
import re
from typing import Any, Dict, List, Tuple

_SW_HDR  = re.compile(r"^\s*(Distributed|Standard)\s+Switch:\s*(.+?)\s*$", re.I)
_VLAN    = re.compile(r"^\s*VLAN\s*ID:\s*(\S+)\s*$", re.I)
_VMK     = re.compile(r"^\s*(vmk\d+)\s*:?\s*(\d{1,3}(?:\.\d{1,3}){3})?\s*$", re.I)
_VMK_HDR = re.compile(r"^\s*VMkernel\s+Ports\s*\((\d+)\)\s*$", re.I)
_VMNIC   = re.compile(r"^\s*(vmnic\d+)\b(.*)$", re.I)
_SLOT    = re.compile(r"^\s*uplink\s*\d+\s*\((\d+)\s*NIC\s*Adapters?\)", re.I)
_SPEED   = re.compile(r"(\d+\s*(?:Gbit/s|Mbit/s|Full|Half)(?:\s*,?\s*(?:Full|Half))?)", re.I)
_NOADAPT = re.compile(r"^\s*No physical network adapters\s*$", re.I)
_NOPG    = re.compile(r"^\s*No associated port groups\s*$", re.I)
_IGNORE  = re.compile(r"^\s*(MANAGE PHYSICAL ADAPTERS|ADD NETWORKING.*|REFRESH|EDIT|"
                      r"Virtual Machines\s*\(\d+\)|Physical Adapters|.*-DVUplinks.*)\s*$", re.I)


def _slice_page(lines: List[str]) -> List[str]:
    """Drop the left menu: start at the LAST 'Virtual switches' (followed by the toolbar or a switch header)."""
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


def parse_vsphere_text_ex(text: str) -> Dict[str, Any]:
    lines = [l.strip() for l in (text or "").replace("\r", "").split("\n") if l.strip()]
    lines = _slice_page(lines)

    recs: List[Dict[str, Any]] = []
    warns: List[str] = []
    gaps: List[Dict[str, Any]] = []
    sw = None
    st: Dict[str, Any] = {}

    def new_rec(**kw):
        r = {"switch": sw, "interface": "", "type": "", "ip": "", "purpose": "", "role": "None", "speed": "", "vlan": ""}
        r.update(kw)
        recs.append(r)
        return r

    def close_card():
        c = st.get("card")
        if c and c.get("expected") is not None and c["expected"] > c["found"]:
            gaps.append({"switch": sw, "kind": "vmk_missing", "card": c["title"], "ocr_can_help": True,
                        "expected": c["expected"], "found": c["found"],
                        "message": f"{sw} / {c['title']}: page says VMkernel Ports ({c['expected']}) but {c['found']} vmk line(s) were pasted."})
        st["card"] = None

    def close():
        nonlocal sw, st
        if sw is None:
            return
        close_card()
        if not st["content"]:
            gaps.append({"switch": sw, "kind": "collapsed", "ocr_can_help": False,
                "message": f"{sw}: collapsed or empty in the pasted text - expand it in vSphere and copy again."})
            warns.append(f"{sw}: collapsed or empty in text - expand it in vSphere and copy again")
        elif st["slots"] > st["nics"]:
            gaps.append({"switch": sw, "kind": "nic_missing", "ocr_can_help": True, "expected": st["slots"], "found": st["nics"],
                "message": f"{sw}: uplink slots list {st['slots']} NIC(s) but only {st['nics']} vmnic line(s) were pasted."})
            warns.append(f"{sw}: uplink slots list {st['slots']} NIC(s) but only {st['nics']} vmnic name(s) in text (expand the uplinks)")
        if st["noadapt"] and st["nics"] == 0:
            new_rec(interface="No Uplink", type="Uplink")

    for i, line in enumerate(lines):
        m = _SW_HDR.match(line)
        if m:
            close()
            sw = m.group(2).strip()
            st = {"content": False, "slots": 0, "nics": 0, "noadapt": False, "card": None, "last_pg": None}
            continue
        if sw is None:
            continue

        if _NOADAPT.match(line):
            st["content"] = True; st["noadapt"] = True; continue
        if _NOPG.match(line):
            st["content"] = True; continue
        ms = _SLOT.match(line)
        if ms:
            st["content"] = True; st["slots"] += int(ms.group(1)); continue
        mh = _VMK_HDR.match(line)
        if mh:
            if st["card"] is not None:
                st["card"]["expected"] = int(mh.group(1))
            continue
        if _IGNORE.match(line) or _VLAN.match(line):
            continue
        mn = _VMNIC.match(line)
        if mn:
            sp = _SPEED.search(mn.group(2) or "")
            new_rec(interface=mn.group(1).lower(), type="Uplink", role="Active Uplink", speed=sp.group(1).strip() if sp else "")
            st["content"] = True; st["nics"] += 1
            continue

        mk = _VMK.match(line)
        if mk:
            pg = st["last_pg"]
            if pg is None:
                pg = new_rec(type="Port Group", purpose=mk.group(1).lower())
            if pg["type"] == "VMkernel":
                # second vmk on the same card -> its own record
                pg = new_rec(type="Port Group", purpose=pg["purpose"], vlan=pg["vlan"])
            pg.update(type="VMkernel", interface=mk.group(1).lower(), ip=mk.group(2) or "")
            if st["card"] is not None:
                st["card"]["found"] += 1
            st["content"] = True
            continue

        # port-group title <=> next line is 'VLAN ID: ...'
        if i + 1 < len(lines) and _VLAN.match(lines[i + 1]):
            close_card()
            v = _VLAN.match(lines[i + 1]).group(1)
            st["last_pg"] = new_rec(type="Port Group", purpose=line, vlan="" if set(v) <= {"-"} else v)
            st["card"] = {"title": line, "expected": None, "found": 0}
            st["content"] = True
            continue
        # anything else (VM names, headings) is ignored on purpose

    close()
    for r in recs:
        if r["type"] == "VMkernel" and not r["ip"]:
            gaps.append({"switch": r["switch"], "kind": "vmk_missing", "card": r["purpose"], "ocr_can_help": True,
                        "expected": 1, "found": 0, "message": f"{r['switch']}: {r['interface']} ({r['purpose']}) has no IP in the pasted text."})
    if not recs and not gaps:
        warns.append("No 'Standard|Distributed Switch:' section recognised in the pasted text.")
    return {"records": recs, "warnings": warns, "gaps": gaps}


def parse_vsphere_text(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    out = parse_vsphere_text_ex(text)
    return out["records"], out["warnings"]

