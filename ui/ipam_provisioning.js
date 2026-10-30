// ui/ipam_provisioning.js
class IPAMProvisioningUI {
    constructor() {
        this.siteName = '';
        this.vlanConfigs = [];
        this.optionVlansEnabled = false;
    }
    
    async loadSiteData(siteName) {
        // Fetch site data from NetBox API via backend
        const response = await fetch(`/api/sites/${siteName}`);
        this.siteData = await response.json();
        return this.siteData;
    }
    
    async provisionPrefixes() {
        const payload = {
            site_name: this.siteName,
            vlan_configs: this.vlanConfigs,
            option_vlans: this.optionVlansEnabled
        };
        
        const response = await fetch('/api/provision/prefixes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        const result = await response.json();
        return result;
    }
    
    renderPrefixPreview(prefixData) {
        const table = document.createElement('table');
        // Render prefix data as table
        return table;
    }
    
    async importToNetbox(prefixData) {
        const response = await fetch('/api/import/netbox', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(prefixData)
        });
        return await response.json();
    }
}