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

