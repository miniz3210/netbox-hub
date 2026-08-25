import re
import json
from typing import Dict
from core.ai_client import call_ai
from config.naming_rules import load_naming_rules, export_rules_as_prompt

def verify_and_suggest_with_ai(user_input_text: str, model_name: str) -> str:
    naming_context = export_rules_as_prompt(load_naming_rules())
    system_msg = f"""You are a Principal Network Architect and NetBox Standard Auditor.
Audit user inputs against these conventions:
{naming_context}

Output Format:
- **Verdict**: [✅ Compliant | 💡 Suggestion]
- **Standard Formula**: `<exact formula pattern>`
- **Recommended Output**: `<standardized string without colons or parentheses>`
- **Audit Reason**: Concise explanation.
"""
    return call_ai(f"Audit:\n```\n{user_input_text}\n```", model_name, custom_system_msg=system_msg)

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