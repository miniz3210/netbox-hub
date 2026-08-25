## Release v2.0.1 — Hardware Specification Precision & Form Factor Guardrails

### 🛠️ Bug Fixes & Improvements
* **NIC Count Guardrails**: Eliminated quad-NIC defaults on compact/tower servers. MicroServers and compact chassis now strictly reflect official datasheet NIC counts (e.g., HPE ProLiant MicroServer Gen8 correctly defaults to 2x 1GbE 332i + 1x dedicated iLO4).
* **Chassis Console Port Verification**: Stopped injecting phantom DB-9/Serial console ports onto tower and desktop hardware lacking external serial COM interfaces.
* **Slug Format Enforcement**: Enforced strict lowercase prefixed slug generation (`<manufacturer>-<model-name>`).
* **Power Supply Sizing Accuracy**: Calibrated single non-redundant PSU wattage draws (e.g., 150W/200W for MicroServers) instead of generic 250W/enterprise defaults.
