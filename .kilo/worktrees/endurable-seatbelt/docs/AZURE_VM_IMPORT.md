# Azure Virtual Machine Import Guide

## Overview

The Azure VM Import feature allows you to import Azure Virtual Machines into NetBox Hub by uploading CSV exports from Azure Portal or using the provided PowerShell script.

## Features

✅ **Automatic VM Detection** - Checks if VMs already exist in the database  
✅ **Azure to NetBox Mapping** - Intelligent mapping of Azure properties to NetBox objects  
✅ **Bulk Import** - Import hundreds of VMs in one operation  
✅ **Update Support** - Option to update existing VM records  
✅ **Validation** - Parse validation with detailed error reporting  
✅ **Export Capability** - Export current VM inventory to CSV  

## Azure to NetBox Field Mapping

| Azure Field | NetBox Destination | Description |
|-------------|-------------------|-------------|
| **SUBSCRIPTION** | Tenant (Azure) | Maps to NetBox Tenant with "Azure" prefix |
| **RESOURCE GROUP** | Cluster / Custom Field | Stored in cluster field and can be used as custom field |
| **LOCATION** | Site (Cloud) | Maps to Site with "Azure - " prefix (e.g., "Azure - Australia East") |
| **SIZE** | Custom Field: Instance Type | VM SKU stored in model_or_role field |
| **OPERATING SYSTEM** | Platform | Windows or Linux platform |
| **NAME** | VM Name | Virtual machine hostname |
| **STATUS** | Description metadata | Running, Stopped, etc. stored in description |
| **PUBLIC IP ADDRESS** | Description metadata | Public IP stored in description if available |
| **DISKS** | Description metadata | Disk count stored in description |

## Quick Start

### Export from Azure Portal

1. Log in to [Azure Portal](https://portal.azure.com)
2. Navigate to **Virtual Machines**
3. Click **Export to CSV** button at the top of the VM list
4. Save the CSV file to your computer
5. In NetBox Hub, go to the **☁️ Azure VMs** tab
6. Upload the CSV file
7. Follow the import wizard

## Import Workflow

### Step 1: Upload CSV

Navigate to **☁️ Azure VMs** tab and upload your CSV file.

**Required CSV columns:**
- NAME
- SUBSCRIPTION
- RESOURCE GROUP
- LOCATION
- STATUS
- OPERATING SYSTEM
- SIZE
- PUBLIC IP ADDRESS
- DISKS
- RESOURCE LINK (optional)

### Step 2: Preview Data

The tool will parse the CSV and display:
- Total VM count
- Running vs. stopped VMs
- Number of subscriptions
- Number of locations
- Data preview table

### Step 3: Map to NetBox

Click **🔄 Map Azure Data to NetBox** to transform Azure data into NetBox format.

The mapping summary shows:
- New VMs to be created
- Existing VMs that will be updated/skipped
- NetBox objects that need to be created:
  - Tenants (subscriptions)
  - Sites (locations)
  - Platforms (OS types)
  - Instance types (VM sizes)
  - Resource groups

### Step 4: Import

Choose your import options:
- ☐ **Update existing VMs** - Check this to update VMs that already exist in the database
- ☐ Leave unchecked to skip existing VMs

Click **💾 Import VMs to NetBox Hub Database** to complete the import.

### Step 5: Review Results

Import statistics will show:
- **Inserted**: New VMs added to database
- **Updated**: Existing VMs that were updated
- **Skipped**: Existing VMs that were not modified
- **Errors**: Any VMs that failed to import

## Database Storage

VMs are stored in the `inventory_records` table with:

```sql
category: 'vm'
name: VM name from Azure
description: Subscription, Resource Group, Status, Public IP, Disks
manufacturer: 'Microsoft Azure'
model_or_role: VM Size (e.g., 'Standard_E2as_v4')
site: 'Azure - <Location>' (e.g., 'Azure - Australia East')
cluster: Resource Group name
```

## Example CSV Format

```csv
NAME,SUBSCRIPTION,RESOURCE GROUP,LOCATION,STATUS,OPERATING SYSTEM,SIZE,PUBLIC IP ADDRESS,DISKS
ANZAPP002,AW-MS-Prod-AUEast-001,rg-anzapp002,Australia East,Running,Windows,Standard_E2as_v4, -,2
AUPDJDEI01,JDE-AuEast-001,rg-app-jde-production,Australia East,Running,Windows,Standard_E2ads_v5, -,2
AU-AZ-WLC02,Corp-SharedServices,Rg-Infra-WLC,Australia East,Running,Linux,Standard_F4s_v2,203.0.113.10,1
```

## Additional Features

### Check VM Status

Enter a VM name in the **🔍 Check VM Status** section to verify if it exists in the database.

### Export Database

Export all VMs currently stored in NetBox Hub to CSV format for backup or analysis.

## Troubleshooting

### Common Issues

**Issue**: "Missing VM name, skipping"  
**Solution**: Ensure the NAME column is not empty in your CSV

**Issue**: "Failed to parse Azure VM CSV"  
**Solution**: Verify your CSV has all required columns with correct headers

**Issue**: PowerShell script fails with authentication error  
**Solution**: Run `Connect-AzAccount` manually and verify you have access to the subscription

**Issue**: No VMs found  
**Solution**: Ensure you have VM Reader role in Azure subscriptions

### Validation

The import tool validates:
- ✅ CSV structure and required columns
- ✅ VM name uniqueness
- ✅ Data type consistency
- ✅ Database connectivity

### Logging

All import operations are logged. Check the application logs for detailed error messages:

```python
import logging
logger = logging.getLogger("netbox-hub")
```

## NetBox Integration

After importing to NetBox Hub, you can export the data to NetBox using:

1. **CSV Export**: Use the export feature to create NetBox-compatible CSVs
2. **API Integration**: Use NetBox Hub's API sync features to push VMs to NetBox
3. **Manual Import**: Export from NetBox Hub and import to NetBox using its built-in CSV import

### NetBox Custom Fields to Create

Before importing to NetBox, create these custom fields:

**For Virtual Machines:**
- `azure_subscription` (Text)
- `azure_resource_group` (Text)
- `azure_instance_type` (Text)
- `azure_resource_link` (URL)

**For Sites:**
- `cloud_provider` (Selection: Azure, AWS, GCP)
- `cloud_region` (Text)

## Best Practices

1. **Test with Small Dataset** - Try importing 5-10 VMs first to verify the mapping
2. **Review Existing VMs** - Check which VMs already exist before deciding to update
3. **Backup Database** - Back up NetBox Hub database before large imports
4. **Incremental Updates** - Run exports regularly to keep NetBox Hub in sync
5. **Name Consistency** - Ensure VM names in Azure match your NetBox naming standards

## Automation

### Scheduled Exports

You can set up scheduled tasks to export VMs from Azure Portal on a regular basis and store them in a shared location for importing into NetBox Hub.

**Tips for Regular Updates:**
- Export VMs weekly or monthly from Azure Portal
- Save exports with timestamps (e.g., `azure-vms-2026-09-05.csv`)
- Keep a history of exports for tracking changes
- Use the update existing VMs option for incremental updates

## Support

For issues or questions:
1. Check the application logs
2. Review this documentation
3. Open an issue in the NetBox Hub repository

## Version History

- **v1.0.0** (2026-09-05)
  - Initial release
  - CSV import support
  - PowerShell export script
  - Azure to NetBox field mapping
  - Duplicate detection
  - Update existing VMs feature
