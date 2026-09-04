# Changelog

## Release v2.4.0 — Session State Manager, Database Indexes & Enhanced IPAM

### 🚀 New Features
* **Session State Manager**: Added centralized session state management in `core/session_manager.py` to reduce scattered `st.session_state` calls (105 instances consolidated into reusable methods)
* **Database Indexes**: Added performance indexes on `ipam_records`, `sites_records`, and `inventory_records` tables for faster lookups
* **AI Verification Buttons**: All interface formatters in Naming tab now include "🤖 AI Verify" buttons
* **Device Type Filtering**: Reference examples now filter by device type (SW, WAP, FW, RTR, ION, VS)
* **Prefix Description from DB**: When loading from database, prefix descriptions are loaded from NetBox instead of generated
* **Site Found Indicator**: Status indicator shows when site data exists in database

### 🛠️ Improvements & Refinements
* **Version Bump**: 2.22 → 2.4.0
* **Data Center VLAN Preset**: Updated to match real NetBox infrastructure patterns from production data
* **Branch VLAN Preset**: Updated based on analysis of 302 VLANs from production data
* **Switch Uplink Description**: Format changed to `Uplink from <Local>_<Port> to <Remote>_<Port>`
* **Switch LAG Member Description**: Format changed to `LACP to <Remote>_<Port>`
* **UI Streamlining**: More compact status indicators for better UX

### 🛠️ Bug Fixes & Improvements
* IPAM Tab crash when VLAN ID was None (TypeError: '<' not supported)
* Naming standards not updating across tabs (added session state reload)
* Load From DB not using database prefix descriptions
* Empty role fields showing instead of database descriptions (quality score de-duplication)
* Quick-Select Test Model refresh not working (removed overly strict filtering)
* Indentation error in ai_client.py

## Release v2.21 — Bundled Export Script, Agent Option Removed & Timestamp Reset

### 🚀 New Features
* **PowerShell Exporter Inside Option A**: The `netbox-export.ps1` script that produces `NetBox_Backup_<timestamp>.json` is now shown directly above the upload button as Step 1, with the run command, a download button and a collapsible full-script view. Step 2 is the JSON upload.

### 🛠️ Improvements & Refinements
* **Removed the PowerShell Agent Option**: The former "Option A/B: Automated Push via PowerShell Agent" block is gone from both the IPAM and Naming ingest panels, along with the embedded `POWERSHELL_AGENT_CODE` payload and its download button. Manual CSV upload is now Option B.
* **Last Update Time Clears With the Data**: Removing records now also clears their sync timestamp, so the CSV rows no longer show a stale "last update time" for data that is no longer in the database. Added `clear_sync_metadata()` and wired it into `clear_sites_records`, `clear_vlans_records`, `clear_prefixes_records`, `clear_device_records`, `clear_vm_records`, `clear_inventory_records` and `clear_ipam_records`.

## Release v2.12 — NetBox Master Backup Upload & Full-Database AI Lookup

### 🚀 New Features
* **Option A: Upload Netbox_Backup**: The IPAM and Naming ingest panels now lead with a JSON uploader for the full `NetBox_Backup_<timestamp>.json` master export.
* **Upload Date & Enable Checkbox**: The uploaded backup is shown with its filename, upload timestamp and object count.
* **Whole-Database AI Access**: `core/backup_manager.py` flattens every object in the backup for searchable AI lookups.
* **Backup Populates Core Tables**: A backup upload also refreshes the Sites, IPAM and Inventory tables.

## Release v2.2.11 — Zero-Dependency Catalog Search & Crashproof Imports

### 🛠️ Bug Fixes & Refinements
* **Zero External Dependencies**: Removed all third-party YAML and Git module requirements.
* **Standard Library I/O**: Switched catalog file indexing to standard Python `os` and `glob` streams.

## Release v2.2.8 — Resilient Tab Imports & Custom Model UI

### 🛠️ Bug Fixes & Improvements
* **Safe Tab Loading**: Added multi-path import handling for all UI tabs.
* **Sidebar Custom Model**: Fixed the active model input field directly in the sidebar.

## Release v2.2.5 — Custom Model Input & Cleaned Defaults

### 🛠️ Improvements & Refinements
* **Custom Model Field**: Renamed on-the-fly test input field to `Custom Model`.
* **Cleaned Default Presets**: Updated default fallback list to verified working models.

## Release v2.2.3 — Site-Specific NetBox Data Matching & Custom AI Model Input

### 🛠️ Improvements & Refinements
* **Wording Normalization**: Standardized label to "NetBox Data" across the application.
* **Site-Aware NetBox Lookup**: Reference cards and AI validation dynamically filter records matching the entered site code.
* **Custom AI Model Input**: Added manual model input in the sidebar for quick testing.

## Release v2.2.2 — Device & Module Library Selection Fix

### 🛠️ Bug Fixes & Improvements
* **Blank Input Library Loading**: Fixed library selection issue with blank model input field.

## Release v2.2.1 — Refined Compact Toolbar & Section Scoping

### 🛠️ Improvements & Refinements
* **Section-Specific Display**: Removed Casing and CSV tools from Section 3.
* **Collapsible Compact Ingest**: Encapsulated the NetBox CSV file uploader into dropdown toolbar.

## Release v2.2.0 — Ultra-Compact Top Bar & Persistent Storage Mount

### 🛠️ Improvements & Refinements
* **Ultra-Compact Ingest Bar**: Reduced uploader height and combined casing selector and reset.
* **Database Persistence Across Reboots**: Ensured database volume mapping guarantees data persistence.

## Release v2.1.9 — Global Auto-Refreshing CSV Ingest & Unified Categorization

### 🛠️ Improvements & Refinements
* **Global CSV Ingest Bar**: Moved the CSV uploader to the top control bar.
* **Unified Categorization**: Single CSV ingestion automatically classifies across all sub-views.
* **Auto-Refresh Trigger**: Uploading or clearing CSV records triggers instant re-rendering.

## Release v2.1.7 — Multi-Category CSV Upload & Database Reset

### 🛠️ Improvements & Refinements
* **Multi-Category CSV Support**: Added distinct NetBox CSV upload support for multiple categories.
* **Database Reset Control**: Added a GUI reset button to clear uploaded reference data.
* **Clean Tab Isolation**: Kept all database modifications isolated within the Naming Generator.

## Release v2.1.6 — Revert Device Types Tab & Isolate NetBox CSV to Naming Generator

### 🛠️ Improvements & Refinements
* **Device Types Tab Reverted**: Restored the original manufacturer and device model catalog search interface.
* **Isolated NetBox CSV Reference Integration**: Moved NetBox CSV upload capability exclusively into Naming Generator.

## Release v2.1.3 — VMkernel Cleanup & NetBox CSV DB Writeback

### 🛠️ Improvements & Refinements
* **VMkernel Description Fallback Fix**: Removed hardcoded "Management" default text.
* **NetBox CSV GUI Importer**: Added file upload capability for NetBox device export spreadsheets.
* **Dynamic Inventory Fallback**: Automatically switches between real database records and fallback examples.

## Release v2.1.1 — ESXi Network Descriptions Enhancements

### 🛠️ Improvements & Refinements
* **VMkernel vSwitch Normalization**: Extended VMware vSwitch auto-correction to VMkernel Adapter fields.
* **Port Group Default Prefix**: Pre-populated Port Group vSwitch input fields with `PG-` by default.
* **Default vmnic Placeholders**: Pre-populated physical uplink and teaming interface inputs.

## Release v2.1.0 — VMware Normalization Bugfix

### 🛠️ Bug Fixes & Improvements
* **Missing Formatter Fix**: Added normalize functions to `utils/formatters.py`.

## Release v2.0.9 — Optional VMware Auto-Correction Toggle

### 🛠️ Improvements & Refinements
* **Auto-Correction Toggle Switch**: Added user toggle to optionally disable automatic formatting.
* **Preserved Raw Input**: When auto-correction is disabled, fields accept exact custom casing.

## Release v2.0.6 — Context-Aware AI Naming Auditor

### 🛠️ Bug Fixes & Improvements
* **Asset-Type Context Scoping**: AI Standards Auditor now receives explicit asset category.
* **Fixed VM Misclassification**: Prevented VM hostnames from being falsely audited as Firewalls.
* **VM Naming Rules Expansion**: Updated rules to recognize regional enterprise formats.

## Release v2.0.5 — Universal Casing Toggle, Blank Defaults & Input Tooltips

### 🛠️ Improvements & Refinements
* **Letter Casing Toggle**: Added UPPERCASE vs. lowercase radio switch.
* **Blank Default States**: Removed hardcoded location defaults.
* **Hover Tooltips**: Added contextual guidance across every input box.
* **Flexible Domain Input**: Replaced rigid domain radio presets with free-text input.
* **Collapsible Reference Examples**: Moved live lookup tables into collapsible containers.

## Release v2.0.4 — Custom Prefix Label Alignment

### 🛠️ Improvements & Refinements
* **Prefix Selector**: Updated input guidance for custom prefix handling.

## Release v2.0.2 — Unified Device Generator & Custom Prefix Expansion

### 🛠️ Improvements & Refinements
* **Unified Device Generator**: Merged separate Switch, AP, and Firewall sections.
* **Custom Prefix Support**: Added direct manual text input support for arbitrary prefixes.
* **Role Alignment**: Normalized roles into unified `Zone / Role` field.
* **Streamlined UI**: Reorganized into clean 2-column layout.

## Release v2.0.1 — Hardware Specification Precision & Form Factor Guardrails

### 🛠️ Bug Fixes & Improvements
* **NIC Count Guardrails**: Eliminated quad-NIC defaults on compact servers.
* **Chassis Console Port Verification**: Stopped injecting phantom serial ports.
* **Slug Format Enforcement**: Enforced strict lowercase prefixed slug generation.
* **Power Supply Sizing Accuracy**: Calibrated PSU wattage draws accurately.
