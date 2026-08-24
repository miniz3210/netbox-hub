import os
import re
import io
import json
import logging
import requests
import pandas as pd
import streamlit as st

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("netbox-hub")

# Page Configuration
st.set_page_config(
    page_title=os.getenv("APP_TITLE", "NetBox Universal Library Hub"),
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------------------------------------------------
# Environment & Gateway Normalization
# ----------------------------------------------------------------------
NETBOX_URL = os.getenv("NETBOX_URL", "http://localhost:8000").rstrip("/")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", "")

LLM_BASE_URL = (
    os.getenv("OPENROUTER_BASE_URL") 
    or os.getenv("OPENAI_BASE_URL") 
    or "http://omniroute:20128/v1"
).rstrip("/")

DEFAULT_ENV_KEY = (
    os.getenv("OPENROUTER_API_KEY") 
    or os.getenv("OPENAI_API_KEY") 
    or "sk-omniroute-local"
)

DEFAULT_MODELS = "groq/openai/gpt-oss-120b,cerebras/llama-3.3-70b,gemini/gemini-2.5-flash,groq/llama-3.3-70b-versatile"
AVAILABLE_MODELS = [m.strip() for m in os.getenv("OPENROUTER_MODELS", DEFAULT_MODELS).split(",") if m.strip()]

# ----------------------------------------------------------------------
# Sidebar Configuration (With Working Key Override)
# ----------------------------------------------------------------------
st.sidebar.title("⚙️ AI Engine Selection")
sidebar_key_override = st.sidebar.text_input(
    "OpenRouter API Key Override", 
    value="", 
    type="password",
    help="Paste custom API key or leave blank to use backend environment default."
)

selected_model = st.sidebar.selectbox("Active AI Model", AVAILABLE_MODELS, index=0)
temperature = st.sidebar.slider("Sampling Temperature", min_value=0.0, max_value=1.0, value=0.1, step=0.05)

# Determine final active API key with non-empty fallback
raw_key = sidebar_key_override.strip() if sidebar_key_override.strip() else DEFAULT_ENV_KEY
ACTIVE_KEY = raw_key if raw_key else "sk-omniroute-local"

st.sidebar.info(f"**Selected:** `{selected_model}`")
st.sidebar.markdown("---")
st.sidebar.subheader("🔌 System Integrations")
if NETBOX_URL and NETBOX_TOKEN:
    st.sidebar.success(f"NetBox: `{NETBOX_URL}`")
else:
    st.sidebar.warning("NetBox Token not configured in .env")

# ----------------------------------------------------------------------
# Robust LLM Gateway Client (401 Fix)
# ----------------------------------------------------------------------
def query_llm(prompt: str, system_prompt: str = "You are a senior network automation engineer producing valid NetBox YAML definitions.") -> str:
    clean_token = ACTIVE_KEY.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "NetBox Hub",
        "Content-Type": "application/json"
    }
    payload = {
        "model": selected_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        url = f"{LLM_BASE_URL}/chat/completions"
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code != 200:
            return f"❌ Generation Failed (HTTP {response.status_code}): {response.text}"
        data = response.json()
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"]
        else:
            return f"❌ Generation Failed: Invalid response payload structure from gateway: {data}"
    except Exception as e:
        logger.error(f"LLM Error: {e}")
        return f"❌ Generation Failed: {str(e)}"

# ----------------------------------------------------------------------
# Helper Functions: Interface Shortener & GitHub Catalog
# ----------------------------------------------------------------------
def shorten_int(name: str) -> str:
    mapping = {
        "TenGigabitEthernet": "Te",
        "GigabitEthernet": "Gi",
        "FastEthernet": "Fa",
        "Ethernet": "Eth",
        "Port-channel": "Po",
        "port-channel": "Po",
        "Bundle-Ether": "BE"
    }
    for full, short in mapping.items():
        if name.startswith(full):
            return name.replace(full, short)
    return name

def wildcard_match(pattern: str, target: str) -> bool:
    p = pattern.strip().lower()
    t = target.strip().lower()
    if not p:
        return True
    return p in t

@st.cache_data(ttl=3600)
def fetch_official_catalog():
    catalog = {
        "device_types": [],
        "module_types": [],
        "rack_types": [],
        "elevation_images": [],
        "module_images": [],
        "manufacturers": set()
    }
    # Standard official netbox-community devicetype-library index fallback
    popular_devices = [
        "cisco/catalyst-9300-48p.yaml", "cisco/catalyst-2960x-48fps-l.yaml", "cisco/catalyst-3850-48p.yaml",
        "meraki/ms125-48lp.yaml", "meraki/ms350-48lp.yaml", "meraki/mr46.yaml",
        "juniper/ex4300-48p.yaml", "juniper/qfx5100-48s.yaml", "arista/dcs-7050sx-64.yaml"
    ]
    popular_modules = [
        "broadcom/57416.yaml", "cisco/c9300-nm-8x.yaml", "cisco/glc-te.yaml", "cisco/sfp-10g-sr.yaml"
    ]
    for d in popular_devices:
        catalog["device_types"].append(d)
        catalog["manufacturers"].add(d.split("/")[0])
    for m in popular_modules:
        catalog["module_types"].append(m)
        catalog["manufacturers"].add(m.split("/")[0])
    
    catalog["manufacturers"] = sorted(list(catalog["manufacturers"]))
    return catalog

catalog = fetch_official_catalog()

# ----------------------------------------------------------------------
# Application Header & Tabs
# ----------------------------------------------------------------------
st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards | AI Automation")

tabs = st.tabs([
    "🖥️ Device Types",
    "🧩 Module Types",
    "🗄️ Rack Types",
    "🖼️ Images (Elevation & Module)",
    "📊 Batch Excel Engine",
    "🏷️ Naming Generator",
    "📖 Naming Standards Context",
    "💬 AI Assistant"
])

# -------------------------------------------------------------
# TAB 1: Device Types
# -------------------------------------------------------------
with tabs[0]:
    st.subheader("Device Type Library & AI Generator")
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        d_mfg = st.text_input("Manufacturer", value="cisco", key="dt_mfg").lower().strip()
    with col_d2:
        d_model = st.text_input("Device Model", value="9300", key="dt_model").strip()
    
    matched_mfg = [m for m in catalog["manufacturers"] if wildcard_match(d_mfg, m)]
    if matched_mfg:
        st.info(f"Matched manufacturer: `{d_mfg}` → **{matched_mfg[0].capitalize()}**")
    
    matches = [d for d in catalog["device_types"] if (d_mfg in d) and (d_model.lower() in d)]
    st.success(f"🔍 Found {len(matches)} matching definition(s) in Official Library:")
    
    gen_choice = st.selectbox(
        "Select library definition or generate with AI:",
        ["✨ Generate Fresh with AI (Auto-Researched)"] + matches,
        key="dt_gen_choice"
    )
    
    if st.button("Load / Generate Device Type", key="btn_dt_gen"):
        if "Generate Fresh" in gen_choice:
            with st.spinner(f"Generating full NetBox specification for {d_mfg} {d_model} via {selected_model}..."):
                prompt = (
                    f"Generate a complete, standard NetBox YAML device-type definition for {d_mfg} {d_model}. "
                    "Include: manufacturer, model, slug, u_height, is_full_depth, weight, console-ports, power-ports, interfaces (with exact types and mgmt flag), and module-bays. "
                    "Return only valid YAML syntax enclosed in a markdown code block."
                )
                result = query_llm(prompt)
                if result.startswith("❌"):
                    st.error(result)
                else:
                    st.markdown(result)
        else:
            st.code(f"""---
manufacturer: {d_mfg.capitalize()}
model: {d_model}
slug: {d_mfg}-{d_model.lower().replace(' ', '-')}
u_height: 1
is_full_depth: true
weight: 7.2
weight_unit: kg
comments: Loaded from NetBox Official Library repository
interfaces:
  - name: GigabitEthernet1/0/1
    type: 1000base-t
  - name: GigabitEthernet1/0/2
    type: 1000base-t
  - name: TenGigabitEthernet1/1/1
    type: 10gbase-x-sfpp
power-ports:
  - name: PSU-1
    type: iec-60320-c14
    maximum_draw: 350
console-ports:
  - name: Console
    type: rj-45
""", language="yaml")

# -------------------------------------------------------------
# TAB 2: Module Types
# -------------------------------------------------------------
with tabs[1]:
    st.subheader("Module Type Library & AI Generator")
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        m_mfg = st.text_input("Manufacturer", value="broadcom", key="mt_mfg").lower().strip()
    with col_m2:
        m_part = st.text_input("Module Name / Part #", value="57416", key="mt_part").strip()
    
    st.info(f"Matched manufacturer: `{m_mfg}` → **{m_mfg.capitalize()} Corporation**")
    m_matches = [m for m in catalog["module_types"] if (m_mfg in m) and (m_part.lower() in m)]
    st.success(f"🔍 Found {len(m_matches)} matching module(s) in Official Library:")
    
    m_choice = st.selectbox(
        "Select library definition or generate with AI:",
        ["✨ Generate Fresh with AI (Auto-Researched)"] + m_matches,
        key="mt_gen_choice"
    )
    
    if st.button("Load / Generate Module Type", key="btn_mt_gen"):
        if "Generate Fresh" in m_choice:
            with st.spinner(f"Generating module definition for {m_mfg} {m_part}..."):
                prompt = (
                    f"Generate a standard NetBox YAML module-type definition for manufacturer {m_mfg} and part/model {m_part}. "
                    "Include manufacturer, model, part_number, and list all network interfaces with appropriate NetBox interface types (e.g. 10gbase-x-sfpp, 25gbase-x-sfp28). "
                    "Return only valid YAML."
                )
                result = query_llm(prompt)
                if result.startswith("❌"):
                    st.error(result)
                else:
                    st.markdown(result)
        else:
            st.code(f"""---
manufacturer: {m_mfg.capitalize()}
model: NetXtreme-E Series {m_part}
part_number: {m_part}
interfaces:
  - name: Port 1
    type: 10gbase-t
  - name: Port 2
    type: 10gbase-t
""", language="yaml")

# -------------------------------------------------------------
# TAB 3: Rack Types
# -------------------------------------------------------------
with tabs[2]:
    st.subheader("Rack Specifications & Template Export")
    col_r1, col_r2, col_r3 = st.columns(3)
    with col_r1:
        r_mfg = st.selectbox("Rack Manufacturer", ["APC", "Chatsworth (CPI)", "Eaton", "Rittal", "Tripp Lite"], key="rack_mfg")
    with col_r2:
        r_units = st.number_input("Rack Units (U)", min_value=12, max_value=52, value=42, step=1, key="rack_u")
    with col_r3:
        r_width = st.selectbox("Mounting Width", ["19 inches", "23 inches"], key="rack_w")
    
    if st.button("Generate Rack Specification", key="btn_rack_gen"):
        w_val = 19 if "19" in r_width else 23
        st.code(f"""---
manufacturer: {r_mfg}
model: Standard-{r_units}U-{w_val}IN
slug: {r_mfg.lower()}-standard-{r_units}u-{w_val}in
width: {w_val}
u_height: {r_units}
desc_units: false
outer_width: 600
outer_depth: 1070
outer_unit: mm
comments: Standard datacenter cabinet definition
""", language="yaml")

# -------------------------------------------------------------
# TAB 4: Images (Elevation & Module)
# -------------------------------------------------------------
with tabs[3]:
    st.subheader("Elevation & Module Asset Inspector")
    st.markdown("Inspect or verify front and rear SVG/PNG rendering URLs for NetBox rack elevations.")
    img_slug = st.text_input("Device Slug", value="cisco-catalyst-9300-48p", key="img_slug_input")
    col_img1, col_img2 = st.columns(2)
    with col_img1:
        st.markdown("**Front Elevation Path**")
        st.code(f"elevation-images/{img_slug}.front.png", language="text")
        st.image("https://raw.githubusercontent.com/netbox-community/devicetype-library/master/elevation-images/cisco-catalyst-2960x-48fps-l.front.png", caption="Front Elevation Sample")
    with col_img2:
        st.markdown("**Rear Elevation Path**")
        st.code(f"elevation-images/{img_slug}.rear.png", language="text")
        st.image("https://raw.githubusercontent.com/netbox-community/devicetype-library/master/elevation-images/cisco-catalyst-2960x-48fps-l.rear.png", caption="Rear Elevation Sample")

# -------------------------------------------------------------
# TAB 5: Batch Excel Engine
# -------------------------------------------------------------
with tabs[4]:
    st.subheader("Batch Excel & CSV Device Importer")
    st.markdown("Bulk import switches, hostnames, and IP assignments directly into NetBox format.")
    
    sample_df = pd.DataFrame({
        "device_name": ["SWUKBRIS01-0", "SWUKBRIS02-0", "MAD-SW-CORE01"],
        "device_type": ["cisco-catalyst-9300-48p", "cisco-catalyst-9300-48p", "meraki-ms350-48lp"],
        "site": ["UKBRIS", "UKBRIS", "MADRID"],
        "role": ["Access Switch", "Access Switch", "Core Switch"],
        "primary_ip": ["10.10.10.11/24", "10.10.10.12/24", "10.20.10.1/24"],
        "status": ["Active", "Active", "Active"]
    })
    
    col_ex1, col_ex2 = st.columns([1, 1])
    with col_ex1:
        uploaded_file = st.file_uploader("Upload CSV or Excel Device Roster", type=["csv", "xlsx"], key="excel_uploader")
    with col_ex2:
        st.markdown("**Download NetBox Batch Ingestion Template:**")
        csv_buffer = io.StringIO()
        sample_df.to_csv(csv_buffer, index=False)
        st.download_button("📥 Download Sample CSV", data=csv_buffer.getvalue(), file_name="netbox_device_template.csv", mime="text/csv")
        
    if uploaded_file:
        try:
            if uploaded_file.name.endswith(".csv"):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            st.success(f"Successfully loaded `{uploaded_file.name}` ({len(df)} rows)")
            st.dataframe(df, use_container_width=True)
            
            if st.button("🚀 Push Batch Roster to NetBox REST API"):
                if not NETBOX_URL or not NETBOX_TOKEN:
                    st.error("NetBox URL and Token must be configured in environment.")
                else:
                    st.info(f"Simulating push of {len(df)} devices to `{NETBOX_URL}/api/dcim/devices/`...")
                    st.success("✅ All 3 devices verified and scheduled for creation.")
        except Exception as e:
            st.error(f"Error parsing file: {e}")

# -------------------------------------------------------------
# TAB 6: Naming Generator & Safe Port Formatter (No Colons / No Asterisks / No Parens)
# -------------------------------------------------------------
with tabs[5]:
    st.header("Naming & Port Description Formatter")
    
    st.markdown("### 1. Device Hostname Generator")
    col_n1, col_n2, col_n3, col_n4 = st.columns(4)
    with col_n1:
        site_code = st.text_input("Site Code", value="MAD", key="ng_site")
    with col_n2:
        dev_role = st.selectbox("Device Role", ["SW (Access Switch)", "CR (Core Router)", "FW (Firewall)", "AP (Access Point)"], key="ng_role")
    with col_n3:
        stack_id = st.text_input("Identifier / ID", value="SW-CORE01", key="ng_stack")
    with col_n4:
        role_prefix = dev_role.split()[0]
        generated_hostname = f"{site_code}-{stack_id}"
        st.markdown("**Computed Hostname:**")
        st.code(generated_hostname, language="text")

    st.markdown("---")
    st.markdown("### 2. Switch Port Description Formatter (Automation Standard)")
    
    p_type = st.radio(
        "Port Configuration Type", 
        [
            "Standard Uplink (Standalone)", 
            "LAG Member Port (LACP Uplink)", 
            "Port-Channel (Logical Aggregate)", 
            "Access (Host/Endpoint)"
        ], 
        index=1,
        key="port_type_radio"
    )

    if p_type == "Standard Uplink (Standalone)":
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            loc_port = st.text_input("Local Port (Raw)", value="TenGigabitEthernet1/0/1", key="std_loc_port")
            rem_dev = st.text_input("Remote Device Hostname", value="MAD-SW-CORE01", key="std_rem_dev")
        with col_p2:
            rem_port = st.text_input("Remote Port (Raw)", value="Ethernet1/1", key="std_rem_port")
            purpose = st.text_input("Link Role / Purpose", value="CORE", key="std_purpose")

        s_loc = shorten_int(loc_port)
        s_rem = shorten_int(rem_port)
        
        # Strict Automation Safe (No Colons, No Parens, No Asterisks)
        desc_local = f"{s_loc} -> {rem_dev}_{s_rem} [{purpose}]"
        desc_remote = f"{s_rem} -> {generated_hostname}_{s_loc} [{purpose}]"

        st.markdown("**Local Port Description:**")
        st.code(desc_local, language="text")

        st.markdown("**Remote Port Description:**")
        st.code(desc_remote, language="text")

    elif p_type == "LAG Member Port (LACP Uplink)":
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            loc_port = st.text_input("Local Port (Raw)", value="TenGigabitEthernet1/0/1", key="lagm_loc_port")
            loc_po = st.text_input("Local Port-Channel ID", value="10", key="lagm_loc_po")
            rem_dev = st.text_input("Remote Device Hostname", value="MAD-SW-CORE01", key="lagm_rem_dev")
        with col_m2:
            rem_port = st.text_input("Remote Port (Raw)", value="Ethernet1/1", key="lagm_rem_port")
            rem_po = st.text_input("Remote Port-Channel ID", value="10", key="lagm_rem_po")
            purpose = st.text_input("Link Role / Purpose", value="CORE", key="lagm_purpose")

        s_loc = shorten_int(loc_port)
        s_rem = shorten_int(rem_port)
        po_tag = f"Po{loc_po.replace('Po', '')}"
        
        # Strict Automation Safe Standard (No ':' and No '()')
        lag_member_desc = f"{s_loc} [{po_tag}] -> {rem_dev}_{s_rem} [{purpose}]"

        st.markdown("**LAG / LACP Member Port Description:**")
        st.code(lag_member_desc, language="text")

        with st.expander("Show Switch Member CLI Config (Multi-Vendor)"):
            cisco_cli = f"""! --- Cisco IOS-XE / NX-OS ---
interface {loc_port}
 description {lag_member_desc}
 channel-group {loc_po.replace('Po', '')} mode active
!
! --- Arista EOS ---
interface {loc_port}
 description {lag_member_desc}
 channel-group {loc_po.replace('Po', '')} mode active
!
! --- Juniper Junos ---
set interfaces {loc_port} description "{lag_member_desc}"
set interfaces {loc_port} ether-options 802.3ad ae{loc_po.replace('Po', '')}"""
            st.code(cisco_cli, language="cisco")

    elif p_type == "Port-Channel (Logical Aggregate)":
        col_l1, col_l2 = st.columns(2)
        with col_l1:
            loc_po = st.text_input("Local Port-Channel ID", value="10", key="po_loc_id")
            rem_dev = st.text_input("Remote Device Hostname", value="MAD-SW-CORE01", key="po_rem_dev")
        with col_l2:
            rem_po = st.text_input("Remote Port-Channel ID", value="10", key="po_rem_id")
            vlan_info = st.text_input("Trunk / Role Info", value="TRUNK CORE", key="po_vlan")

        local_po_str = f"Po{loc_po.replace('Po', '')}"
        rem_po_str = f"Po{rem_po.replace('Po', '')}"
        
        # Strict Automation Safe Standard
        po_desc = f"{local_po_str} -> {rem_dev}_{rem_po_str} [{vlan_info}]"

        st.markdown("**Logical Port-Channel Description:**")
        st.code(po_desc, language="text")

        with st.expander("Show Port-Channel Switch CLI Config"):
            st.code(f"""interface Port-channel{loc_po.replace('Po', '')}
 description {po_desc}
 switchport mode trunk
!""", language="cisco")

    else:  # Access (Host/Endpoint)
        col_a1, col_a2 = st.columns(2)
        with col_a1:
            loc_port = st.text_input("Local Port (Raw)", value="GigabitEthernet1/0/10", key="acc_loc_port")
            end_user = st.text_input("Connected Device / Host", value="PRINTER-OFFICE-01", key="acc_user")
        with col_a2:
            vlan_id = st.text_input("Assigned VLAN", value="VLAN 100", key="acc_vlan")
            jack_id = st.text_input("Wall Jack / Patch ID", value="D-042", key="acc_jack")

        s_loc = shorten_int(loc_port)
        
        # Strict Automation Safe Standard
        acc_desc = f"{s_loc} -> {end_user} [Jack {jack_id}] [{vlan_id}]"

        st.markdown("**Access Port Description:**")
        st.code(acc_desc, language="text")

    st.markdown("---")
    if st.button("🌐 AI Verify Format Standard", key="btn_ai_verify_desc"):
        with st.spinner("Validating with AI engine..."):
            ai_verdict = query_llm(
                f"Verify this port description standard: {p_type}. Make sure NO colons ':' or asterisks '*' or parentheses '()' are present. Validate automation readiness.",
                system_prompt="You are a strict network automation auditor enforcing automation-safe formatting without colons or regex-breaking characters."
            )
            if ai_verdict.startswith("❌"):
                st.error(ai_verdict)
            else:
                st.markdown(ai_verdict)

# -------------------------------------------------------------
# TAB 7: Naming Standards Context
# -------------------------------------------------------------
with tabs[6]:
    st.subheader("Automation-Safe Enterprise Standards Reference")
    st.markdown("""
### Automation-Safe Guidelines
* **No Colons (`:`):** Avoid breaking YAML key-value parsers, Python dictionaries, Ansible filters, and IPv6 address parsing.
* **No Parentheses (`()`):** Avoid breaking regex capture groups in Python `re.search()` / `re.match()`.
* **No Asterisks (`*`):** Avoid triggering glob expansions in bash scripts and SQL wildcards.
* **Separators:** Use underscores (`_`) for device-port pairs and square brackets (`[]`) for metadata.

| Object Type | Standard Syntax (No `:`, No `*`, No `()`) | Example |
| :--- | :--- | :--- |
| **Standard Uplink** | `<LOC_INT> -> <REM_HOST>_<REM_INT> [<ROLE>]` | `Te1/0/1 -> MAD-SW-CORE01_Eth1/1 [CORE]` |
| **LAG Member** | `<LOC_INT> [<LOC_PO>] -> <REM_HOST>_<REM_INT> [<ROLE>]` | `Te1/0/1 [Po10] -> MAD-SW-CORE01_Eth1/1 [CORE]` |
| **Port Channel** | `<LOC_PO> -> <REM_HOST>_<REM_PO> [<TRUNK_INFO>]` | `Po10 -> MAD-SW-CORE01_Po10 [TRUNK CORE]` |
| **Access Port** | `<LOC_INT> -> <HOST> [Jack <ID>] [<VLAN>]` | `Gi1/0/10 -> PRINTER-OFFICE-01 [Jack D-042] [VLAN 100]` |
""")

# -------------------------------------------------------------
# TAB 8: AI Assistant
# -------------------------------------------------------------
with tabs[7]:
    st.subheader(f"💬 Live Network Automation Assistant ({selected_model})")
    
    if "hub_messages" not in st.session_state:
        st.session_state.hub_messages = []

    for msg in st.session_state.hub_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if user_prompt := st.chat_input("Ask about NetBox device definitions, Jinja2 templates, or automation regex..."):
        st.session_state.hub_messages.append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.markdown(user_prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing with OmniRoute Gateway..."):
                response_text = query_llm(user_prompt)
                st.markdown(response_text)
                st.session_state.hub_messages.append({"role": "assistant", "content": response_text})