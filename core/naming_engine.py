import re
import json
from typing import Dict, Optional
from core.ai_client import call_ai
from config.naming_rules import load_naming_rules, export_rules_as_prompt

def verify_and_suggest_with_ai(user_input_text: str, model_name: str, asset_type: str = "General Asset") -> str:
    naming_context = export_rules_as_prompt(load_naming_rules())
    system_msg = f"""You are a Principal Infrastructure Architect and NetBox Standard Auditor.
You are evaluating an asset of type: **{asset_type}**.
DO NOT confuse this asset type with other categories (e.g., do NOT evaluate a Virtual Machine as a Firewall even if the name contains 'FW').

Audit the input strictly against the following company standards:
{naming_context}

Output Format:
- **Verdict**: [✅ Compliant | 💡 Suggestion]
- **Target Asset Class**: {asset_type}
- **Standard Formula**: `<exact formula pattern for {asset_type}>`
- **Recommended Output**: `<standardized string>`
- **Audit Reason**: Concise explanation of the evaluation.
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