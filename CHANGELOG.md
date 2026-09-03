## Release v2.0.1 — Hardware Specification Precision & Form Factor Guardrails

### 🛠️ Bug Fixes & Improvements
* **NIC Count Guardrails**: Eliminated quad-NIC defaults on compact/tower servers. MicroServers and compact chassis now strictly reflect official datasheet NIC counts (e.g., HPE ProLiant MicroServer Gen8 correctly defaults to 2x 1GbE 332i + 1x dedicated iLO4).
* **Chassis Console Port Verification**: Stopped injecting phantom DB-9/Serial console ports onto tower and desktop hardware lacking external serial COM interfaces.
* **Slug Format Enforcement**: Enforced strict lowercase prefixed slug generation (`<manufacturer>-<model-name>`).
* **Power Supply Sizing Accuracy**: Calibrated single non-redundant PSU wattage draws (e.g., 150W/200W for MicroServers) instead of generic 250W/enterprise defaults.

## Release v2.0.2 — Unified Device Generator & Custom Prefix Expansion

### 🛠️ Improvements & Refinements
* **Unified Device Generator**: Merged separate Switch, AP, and Firewall sections into a single standard generator (`Prefix` + `Country` + `State` + `Site` + `Zone/Role` + `Seq` + `StackID`).
* **Custom Prefix Support**: Added direct manual text input support for arbitrary prefixes (`✏️ Custom Prefix...`).
* **Role Alignment**: Normalized Panorama, Firewall vendor tags, and switch roles into the unified `Zone / Role` field.
* **Streamlined UI**: Reorganized Section 1 into a clean, 2-column layout (Device Hostname Generator + Interface Formatter).

## Release v2.0.4 — Custom Prefix Label Alignment

### 🛠️ Improvements & Refinements
* **Prefix Selector**: Retained `✏️ Custom Prefix...` as the label and updated input guidance to indicate leaving the field empty produces a blank prefix.

## Release v2.0.5 — Universal Casing Toggle, Blank Defaults & Input Tooltips

### 🛠️ Improvements & Refinements
* **Letter Casing Toggle**: Added UPPERCASE (default) vs. lowercase radio switch applied dynamically across all generated hostnames.
* **Blank Default States**: Removed hardcoded location defaults; all input fields now start clean.
* **Hover Tooltips (`help=...`)**: Added contextual guidance and format examples across every input box.
* **Flexible Domain Input**: Replaced rigid domain radio presets with a free-text FQDN input supporting any custom domain or shortname.
* **Collapsible Reference Examples**: All live lookup reference tables moved into collapsible `st.expander` containers.

## Release v2.0.6 — Context-Aware AI Naming Auditor

### 🛠️ Bug Fixes & Improvements
* **Asset-Type Context Scoping**: The AI Standards Auditor now receives the explicit asset category (`Virtual Machine`, `ESXi Host`, `Network Switch`, or `Firewall`).
* **Fixed VM Misclassification**: Prevented VM hostnames containing substrings like `FW` (e.g. `AURFWOTAPP01`) from being falsely audited as Firewalls.
* **VM Naming Rules Expansion**: Updated rules context to recognize regional enterprise formats `<Country><Site><Role><Seq>` and `<SitePrefix><Role><Seq>`.

## Release v2.0.9 — Optional VMware Auto-Correction Toggle

### 🛠️ Improvements & Refinements
* **Auto-Correction Toggle Switch**: Added a user toggle (`⚡ Auto-Correct VMware Syntax`) to optionally disable automatic formatting and allow raw, unformatted manual inputs.
* **Preserved Raw Input**: When auto-correction is disabled, fields accept exact custom casing and naming without modification.

## Release v2.1.0 — VMware Normalization Bugfix

### 🛠️ Bug Fixes & Improvements
* **Missing Formatter Fix**: Added `normalize_vswitch`, `normalize_vmnic`, and `normalize_vmnic_list` to `utils/formatters.py` to resolve import errors.


## Release v2.1.1 — ESXi Network Descriptions Enhancements

### 🛠️ Improvements & Refinements
* **VMkernel vSwitch Normalization**: Extended VMware vSwitch auto-correction to the VMkernel Adapter vSwitch input field.
* **Port Group Default Prefix (`PG-`)**: Pre-populated Port Group vSwitch input fields with `PG-` by default.
* **Default vmnic Placeholders**: Pre-populated physical uplink and teaming interface inputs with `vmnicX` standards.

## Release v2.1.3 — VMkernel Cleanup & NetBox CSV DB Writeback

### 🛠️ Improvements & Refinements
* **VMkernel Description Fallback Fix**: Removed hardcoded "Management" default text when Purpose / Service is left blank.
* **NetBox CSV GUI Importer**: Added file upload capability for NetBox device export spreadsheets with SQLite database writeback and persistence.
* **Dynamic Inventory Fallback**: Automatically switches between real database records and fallback mock examples depending on upload state.

## Release v2.1.6 — Revert Device Types Tab & Isolate NetBox CSV to Naming Generator

### 🛠️ Improvements & Refinements
* **Device Types Tab Reverted**: Restored the original manufacturer and device model catalog search interface.
* **Isolated NetBox CSV Reference Integration**: Moved NetBox CSV upload capability exclusively into the Naming Generator's reference example expander to dynamically swap dummy examples with real database records.

## Release v2.1.7 — Multi-Category CSV Upload & Database Reset

### 🛠️ Improvements & Refinements
* **Multi-Category CSV Support**: Added distinct NetBox CSV upload support for Network Devices, ESXi Hypervisors, and Virtual Machines.
* **Database Reset Control**: Added a GUI reset button to clear uploaded reference data and revert to default examples.
* **Clean Tab Isolation**: Kept all database modifications isolated within the Naming Generator.

## Release v2.1.9 — Global Auto-Refreshing CSV Ingest & Unified Categorization

### 🛠️ Improvements & Refinements
* **Global CSV Ingest Bar**: Moved the CSV uploader to the top control bar next to Letter Casing Mode.
* **Unified Categorization**: Single CSV ingestion automatically classifies and populates Network Devices, ESXi Hypervisors, and Virtual Machines across all sub-views.
* **Auto-Refresh Trigger**: Uploading or clearing CSV records triggers instant re-rendering of reference examples and AI context.

## Release v2.2.0 — Ultra-Compact Top Bar & Persistent Storage Mount

### 🛠️ Improvements & Refinements
* **Ultra-Compact Ingest Bar**: Reduced uploader height and combined casing selector and reset into a streamlined single-line banner.
* **Database Persistence Across Reboots**: Ensured database volume mapping guarantees data persistence until explicitly cleared.

## Release v2.2.1 — Refined Compact Toolbar & Section Scoping

### 🛠️ Improvements & Refinements
* **Section-Specific Display**: Removed Casing and CSV tools completely from Section 3 (ESXi Network Descriptions).
* **Collapsible Compact Ingest**: Encapsulated the NetBox CSV file uploader into an unobtrusive dropdown toolbar in Sections 1 and 2.

## Release v2.2.2 — Device & Module Library Selection Fix

### 🛠️ Bug Fixes & Improvements
* **Blank Input Library Loading**: Fixed an issue where selecting an item from the library dropdown with a blank model input field resulted in no response. Model names are now derived from the selected library definition path.

APP_VERSION = "v2.2.3"
GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
RULES_FILE = "naming_rules.json"

## Release v2.2.3 — Site-Specific NetBox Data Matching & Custom AI Model Input

### 🛠️ Improvements & Refinements
* **Wording Normalization**: Standardized label to "NetBox Data" across the application.
* **Site-Aware NetBox Lookup**: Reference cards and AI validation dynamically filter records matching the entered site code (e.g., `AGE`, `DAL`).
* **Custom AI Model Input**: Added manual model input in the sidebar for quick testing of any gateway model.

## Release v2.2.5 — Custom Model Input & Cleaned Defaults

### 🛠️ Improvements & Refinements
* **Custom Model Field**: Renamed on-the-fly test input field to `Custom Model`.
* **Cleaned Default Presets**: Updated default fallback list to verified working models (`gemini-3-flash-preview`, `qwen3.6-27b`, `gpt-oss-120b`).

## Release v2.2.8 — Resilient Tab Imports & Custom Model UI

### 🛠️ Bug Fixes & Improvements
* **Safe Tab Loading**: Added multi-path import handling for all UI tabs to prevent `ModuleNotFoundError`.
* **Sidebar Custom Model**: Fixed the active model input field directly in the sidebar.

## Release v2.2.11 — Zero-Dependency Catalog Search & Crashproof Imports

### 🛠️ Bug Fixes & Refinements
* **Zero External Dependencies**: Removed all third-party YAML and Git module requirements from `catalog.py` and `device_tab.py`, eliminating `ModuleNotFoundError`.
* **Standard Library I/O**: Switched catalog file indexing to standard Python `os` and `glob` streams.

## Release v2.12 — NetBox Master Backup Upload & Full-Database AI Lookup

### 🚀 New Features
* **Option A: Upload Netbox_Backup**: The IPAM and Naming ingest panels now lead with a JSON uploader for the full `NetBox_Backup_<timestamp>.json` master export. The PowerShell agent moved to Option B and manual CSV upload to Option C.
* **Upload Date & Enable Checkbox**: The uploaded backup is shown with its filename, upload timestamp and object count, plus a checkbox that includes or excludes it from AI Assistant lookups without deleting it, and a Remove Backup button.
* **Whole-Database AI Access**: `core/backup_manager.py` flattens every object in the backup (sites, regions, racks, manufacturers, device types, roles, platforms, devices, interfaces, VRFs, VLANs, prefixes, IP addresses, clusters, VMs, tenants, circuit providers, circuits) into a searchable table so the AI Assistant can answer questions about any record, not just devices and prefixes.
* **Relevance-Ranked Context**: Prompts are split into exact identifiers (hostnames, CIDRs, IPs, cluster names) and ranked keywords, then mapped onto the matching NetBox object types, with `total in backup` counts included so counting questions are answered exactly.
* **Backup Populates Core Tables**: A backup upload also refreshes the Sites, IPAM and Inventory tables, so Scope ID lookup, supernet discovery, reference cards and CSV generators work straight from the backup.

### 🛠️ Bug Fixes & Improvements
* **VLANs Without Prefixes Retained**: `save_ipam_records_batch` no longer discards VLAN records that have no assigned prefix, so VLAN-only rows survive ingest.
* **VLAN Site Inference**: VLANs scoped only by VLAN group (e.g. `Adelaide VLAN Group`) are matched back to their site.
* **IP Ownership Resolution**: IP address records now surface the device or VM behind the assigned interface.
* **Fixed Undefined DB Path**: `lookup_vlan_description_from_db` referenced an undefined `DATABASE_PATH` and always failed silently; it now uses `DB_PATH`.
* **Repaired `.gitignore`**: Removed a stray `cat << 'EOF'` heredoc wrapper that made the first and last lines non-functional, and added the master backup JSON files.

## Release v2.21 — Bundled Export Script, Agent Option Removed & Timestamp Reset

### 🚀 New Features
* **PowerShell Exporter Inside Option A**: The `netbox-export.ps1` script that produces `NetBox_Backup_<timestamp>.json` is now shown directly above the upload button as Step 1, with the run command, a download button and a collapsible full-script view. Step 2 is the JSON upload.

### 🛠️ Improvements & Refinements
* **Removed the PowerShell Agent Option**: The former "Option A/B: Automated Push via PowerShell Agent" block is gone from both the IPAM and Naming ingest panels, along with the embedded `POWERSHELL_AGENT_CODE` payload and its download button. Manual CSV upload is now Option B.
* **Last Update Time Clears With the Data**: Removing records now also clears their sync timestamp, so the CSV rows no longer show a stale "last update time" for data that is no longer in the database. Added `clear_sync_metadata()` and wired it into `clear_sites_records`, `clear_vlans_records`, `clear_prefixes_records`, `clear_device_records`, `clear_vm_records`, `clear_inventory_records` and `clear_ipam_records`.
