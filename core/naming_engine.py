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
    # Load naming rules from session state if available (updated by Standards tab), otherwise from file
    import streamlit as st
    if "naming_rules" in st.session_state:
        naming_rules = st.session_state["naming_rules"]
        naming_context = export_rules_as_prompt(naming_rules)
    else:
        naming_context = export_rules_as_prompt(load_naming_rules())
    
    inventory_context = build_inventory_context_for_ai(category_key, site_filter=site_filter)

    system_msg = f"""You are a Principal Infrastructure Architect and NetBox Standards Auditor.
Evaluate the following asset: **{asset_type}**.

{inventory_context}

STRICT AUDIT INSTRUCTIONS:
1. If matched NetBox Data records are provided above for this site/cluster, align your audit and recommendations to match the proven site codes, role conventions, and patterns observed in those records.
2. Accept enterprise internal domain suffixes (e.g. `.internal`, `.corp`, `.adds`, `.local`, `.lan`) as valid private directory structures.
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
  "branch_switch": "...", "branch_stack": "...", "branch_ap": "...",
  "branch_firewall": "...", "branch_ion": "...", "branch_router": "...", "branch_va": "...",
  "branch_security": "...",
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


def generate_autocorrect_rule(natural_language: str, model_name: str) -> Dict[str, str]:
    """Generate a regex auto-correction rule from a natural language description.

    Returns a dict with ``pattern``, ``replacement`` and ``description`` suitable for
    the "Add Rule" inputs in the Standards tab. Raises on invalid/missing JSON.
    """
    system_msg = (
        "You are an expert Python regular expression engineer for network infrastructure "
        "naming auto-correction. Convert the user's plain-English formatting request into a "
        "single correction rule.\n"
        "STRICT OUTPUT RULES:\n"
        "1. Return ONLY valid raw JSON with exactly these keys:\n"
        '   {"pattern": "<regex>", "replacement": "<replacement>", "description": "<short human description>"}\n'
        "2. pattern MUST be a valid Python regex. Use the case-insensitive flag (?i) at the "
        "start wherever case may vary, use \\\\b word boundaries, and wrap the parts of the "
        "match that must be preserved into capture groups like (\\\\d+) or (\\\\w+) so the "
        "replacement can reference them with \\\\1, \\\\2, etc.\n"
        "3. replacement MUST be a valid Python replacement string referencing capture groups, e.g. "
        "VLAN\\\\2 or Po\\\\1.\n"
        "4. description MUST be a concise plain-English summary of what the rule does.\n"
        "5. Do NOT wrap the JSON in markdown code fences or any other text."
    )

    prompt = (
        f"Given this formatting requirement, produce the auto-correction rule:\n"
        f'"{natural_language}"\n'
        "Return ONLY the raw JSON object described above."
    )

    raw_res = call_ai(prompt, model_name, custom_system_msg=system_msg)
    clean_json = re.sub(r"^```(?:json)?|```$", "", raw_res.strip(), flags=re.IGNORECASE).strip()
    try:
        data = json.loads(clean_json)
    except json.JSONDecodeError:
        # Try to salvage a "pattern"/"replacement"/"description" JSON object if the model
        # wrapped it in outer braces or stray text.
        match = re.search(r"\{.*\}", clean_json, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError("AI did not return a JSON object.")
    pattern = data.get("pattern")
    replacement = data.get("replacement")
    description = data.get("description", "")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("AI response missing a valid 'pattern'.")
    if not isinstance(replacement, str):
        replacement = ""
    return {
        "pattern": pattern,
        "replacement": replacement,
        "description": description if isinstance(description, str) else "",
    }


def generate_naming_pattern(description: str, model_name: str) -> str:
    """Generate a single naming pattern token template from a natural-language description.

    Returns a raw pattern string (e.g. ``SAN<Country><Site><Seq>``) using valid
    NetBox Hub tokens. Raises on failure.
    """
    system_msg = (
        "You are an expert network infrastructure naming convention engineer.\n"
        "Convert the user's plain-English naming requirement into ONE naming pattern string.\n"
        "STRICT RULES:\n"
        "1. Return ONLY the raw pattern string — no JSON, no explanation, no code fences.\n"
        "2. Use ONLY NetBox Hub tokens from this list: <Country>, <State>, <Site>, <Zone>, "
        "<Vendor>, <Seq>, <StackID>, <Role>, <site_prefix>, <role_esx>, <host_seq>, "
        "<Domain>, <domain>, <vmnic>, <vSwitch>, <Purpose>, <Status>, <pg_network>, "
        "<PortGroup>, <Active_vmnics>, <Standby_vmnics>, <vmk>.\n"
        "3. The pattern MUST start with a short prefix (e.g. SAN, PDU, OOB, BLD, DCIM).\n"
        "4. Tokens are case-sensitive and wrapped in angle brackets.\n"
        "5. Do NOT include any extra text, markdown, or formatting."
    )

    prompt = f"Given this naming requirement, produce only the pattern string:\n\"{description}\""

    raw_res = call_ai(prompt, model_name, custom_system_msg=system_msg)
    result = raw_res.strip()
    # Strip any accidental markdown fences
    result = re.sub(r"^```(?:text)?\s*|```$", "", result, flags=re.IGNORECASE).strip()
    return result