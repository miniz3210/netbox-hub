import re
import json
from typing import Dict
from core.ai_client import call_ai
from config.naming_rules import load_naming_rules, export_rules_as_prompt

def verify_and_suggest_with_ai(user_input_text: str, model_name: str, asset_type: str = "General Asset") -> str:
    naming_context = export_rules_as_prompt(load_naming_rules())
    system_msg = f"""You are a Principal Infrastructure Architect and NetBox Standards Auditor.
Evaluate the following asset of type: **{asset_type}**.

STRICT AUDIT INSTRUCTIONS:
1. Active Directory suffixes like `.eswine.adds`, `.adds`, `.aw.ads`, and `.eswines.ot` are 100% VALID official enterprise internal domains. DO NOT flag `.adds` or `.ot` as invalid or non-standard TLDs.
2. Recognize both standard site codes (e.g., `pws`, `age`, `cam`, `rofl`, `syd`, `bris`) and OT role prefixes (e.g., `otinfhost`, `otinfesx`, `esx`, `infmgmt`).
3. For ESXi hosts with `.eswine.adds`, evaluate it as an official Corporate/IT Hypervisor node.
4. Output Format:
- **Verdict**: [✅ Compliant | 💡 Suggestion]
- **Target Asset Class**: {asset_type}
- **Standard Formula**: `<exact formula matching company pattern>`
- **Recommended Output**: `<clean recommended hostname>`
- **Audit Reason**: Clear explanation acknowledging the company domain and site structure.

COMPANY NAMING STANDARDS:
{naming_context}
"""
    prompt = f"Audit this {asset_type}:\n```\n{user_input_text}\n```"
    return call_ai(prompt, model_name, custom_system_msg=system_msg)

def parse_prompt_to_rules(prompt_text: str, model_name: str) -> Dict[str, str]:
    extract_prompt = f"""
Analyze this natural language naming standard and return a valid JSON matching this schema:
{{
  "branch_switch": "...", "branch_ap": "...", "branch_security": "...",
  "switch_uplink_desc_local": "...", "switch_uplink_desc_remote": "...",
  "switch_lag_member": "...", "switch_port_channel": "...", "switch_access_desc": "...",
  "firewall_interface": "...", "esxi_host": "...", "vm_host": "...",
  "esxi_uplink": "...", "esxi_portgroup": "...", "esxi_vmkernel": "...", "netbox_server_yaml": "..."
}}
Input Prompt:
{prompt_text}
Output ONLY raw JSON.
"""
    raw_res = call_ai(extract_prompt, model_name)
    clean_json = re.sub(r"^```(?:json)?|```$", "", raw_res.strip(), flags=re.IGNORECASE).strip()
    return json.loads(clean_json)