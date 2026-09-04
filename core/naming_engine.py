import re
import json
from typing import Dict, List, Any
from core.ai_client import call_ai
from config.naming_rules import load_naming_rules, export_rules_as_prompt
from core.db_manager import get_records_by_category

def build_inventory_context_for_ai(category: str, site_filter: str = "") -> str:
    """Builds a contextual summary of actual uploaded NetBox Data records."""
    records = get_records_by_category(category, site_filter=site_filter)
    if not records:
        return "No matching NetBox Data records present. Evaluate strictly against standard enterprise guidelines."
    
    samples = []
    for r in records[:15]:
        site_str = f", Site: {r['site']}" if r.get('site') else ""
        desc_info = f" - {r['description']}" if r.get('description') else ""
        type_info = f" [{r.get('model_or_role') or ''}{site_str}]"
        samples.append(f"• {r['name']}{type_info}{desc_info}")
    
    site_notice = f" for Site '{site_filter.upper()}'" if site_filter else ""
    return f"MATCHED NETBOX DATA CONTEXT{site_notice}:\n" + "\n".join(samples)

def verify_and_suggest_with_ai(user_input_text: str, model_name: str, asset_type: str = "General Asset", category_key: str = "device", site_filter: str = "") -> str:
    naming_context = export_rules_as_prompt(load_naming_rules())
    inventory_context = build_inventory_context_for_ai(category_key, site_filter=site_filter)

    system_msg = f"""You are a Principal Infrastructure Architect and NetBox Standards Auditor.
Evaluate the following asset: **{asset_type}**.

{inventory_context}

STRICT AUDIT INSTRUCTIONS:
1. If matched NetBox Data records are provided above for this site/cluster, align your audit and recommendations to match the proven site codes, role conventions, and patterns observed in those records.
2. Accept enterprise internal domain suffixes (e.g. `.internal`, `.corp`, `.adds`, `.local`, `.eswine.adds`, `.lan`) as valid private directory structures.
3. Output Format:
- **Verdict**: [✅ Compliant | 💡 Suggestion]
- **Target Asset Class**: {asset_type}
- **Observed Site / NetBox Pattern**: <Explain pattern based on NetBox Data records if present>
- **Recommended Output**: `<clean recommended hostname or syntax>`
- **Audit Reason**: Clear concise architectural explanation.

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
  "switch_uplink_desc": "...",
  "switch_lag_member": "...", "switch_port_channel": "...", "switch_access_desc": "...",
  "firewall_interface": "...", "esxi_host": "...", "vm_host": "...",
  "esxi_uplink": "...", "esxi_portgroup": "...", "esxi_vmkernel": "...", "netbox_server_yaml": "..."
}}
Input Prompt:
{prompt_text}
Output ONLY raw JSON.
"""
    raw_res = call_ai(extract_prompt, model_name, custom_system_msg="You are an expert JSON generator for network naming standards. Output ONLY valid, raw JSON.")
    clean_json = re.sub(r"^```(?:json)?|```$", "", raw_res.strip(), flags=re.IGNORECASE).strip()
    return json.loads(clean_json)