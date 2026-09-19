<#
.SYNOPSIS
    NetBox Minimal Backup - Essential Data Only

.DESCRIPTION
    Ingest NetBox Site Data (Backup / CSV)
    
    This script performs a minimal backup of essential NetBox data required for
    site restoration. Exports only core infrastructure, network, and configuration
    data, excluding audit logs, runtime data, and user accounts.

.VERSION
    3.1.1

.BACKUP CONTENTS
    The following essential data types are exported and can be ingested via PowerShell (PS) or manual CSV upload (CSV):
    
    DCIM - Core Infrastructure:
    • Sites, Regions, Site Groups, Locations (PS/CSV)
    • Racks, Rack Roles, Rack Groups, Rack Types (PS/CSV)
    • Manufacturers, Device Types, Device Roles, Platforms (PS/CSV)
    • Devices, Interfaces (PS/CSV)
    • Modules, Module Types, Module Bays (PS/CSV)
    • Cables, Cable Terminations (PS)
    • Console Ports, Console Server Ports (PS/CSV)
    • Power Ports, Power Outlets, Power Panels, Power Feeds (PS/CSV)
    
    IPAM - Network Essentials:
    • VRFs, RIRs, ASNs, ASN Ranges, Aggregates (PS/CSV)
    • Prefixes, IP Addresses, IP Ranges (PS/CSV)
    • VLANs, VLAN Groups, Roles (PS/CSV)
    • Services, Service Templates (PS/CSV)
    • FHRP Groups, FHRP Group Assignments (PS)
    
    Virtualization:
    • Cluster Types, Cluster Groups, Clusters (PS/CSV)
    • Virtual Machines, Virtual Machine Types (PS/CSV)
    • VM Interfaces, Virtual Disks (PS/CSV)
    
    Circuits:
    • Providers, Provider Accounts, Provider Networks (PS/CSV)
    • Circuit Types, Circuits, Circuit Terminations (PS/CSV)
    
    Tenancy:
    • Tenants, Tenant Groups (PS/CSV)
    • Contacts, Contact Groups, Contact Roles, Contact Assignments (PS/CSV)
    
    Configuration Only:
    • Tags, Custom Fields (PS/CSV)
    • Config Contexts, Config Templates (PS)

.CUSTOM FIELD CHOICE SETS
    Essential custom field choice sets for core operations:
    
    • Instance Type Set (VM/Device sizes) - PS/CSV
    • Operating System Set (OS platforms) - PS/CSV
    • Environment Set (Prod/Dev/Test) - PS/CSV
    • Status Set (Active/Inactive) - PS/CSV
    • All custom fields exported with choice set definitions - PS/CSV

.EXCLUDED FROM MINIMAL BACKUP
    The following are NOT included in minimal backups:
    
    ✗ Audit logs (object-changes, journal-entries)
    ✗ Background jobs, tasks, queues, workers
    ✗ User accounts, tokens, permissions, groups
    ✗ Bookmarks, subscriptions, UI preferences
    ✗ Plugin-specific data
    ✗ Image attachments
    ✗ Notification history
    ✗ Event rules and webhooks execution history

.INGEST OPTIONS
    Option A: PowerShell Backup (Recommended)
    - Run this script to export minimal JSON backup
    - Upload JSON file to NetBox Hub via "🔄 NetBox Backup" tab
    - Core data automatically ingested with relationships intact
    - Smaller file size, faster processing
    
    Option B: Manual CSV Upload
    - Export individual object types from NetBox as CSV
    - Upload via NetBox Hub CSV import interfaces
    - Suitable for specific object type updates
    - May require multiple uploads to maintain relationships

.PARAMETER NetBoxUrl
    The base URL of your NetBox instance (must start with https://)

.PARAMETER ApiToken
    NetBox API authentication token with read permissions

.PARAMETER PageSize
    Number of records to fetch per API request (1-10000, default: 1000)

.PARAMETER OutputDirectory
    Directory to save backup files (default: current directory)

.EXAMPLE
    .\netbox-export-min.ps1 -NetBoxUrl "https://netbox.example.com" -ApiToken "abc123..."
    
.EXAMPLE
    .\netbox-export-min.ps1 -NetBoxUrl "https://netbox.example.com" -ApiToken "abc123..." -OutputDirectory "C:\Backups" -PageSize 500

.NOTES
    - Exports only essential endpoints for site restoration
    - Automatically handles pagination for large datasets
    - Validates JSON output before finalizing
    - Creates timestamped backup and log files
    - Non-destructive read-only operation
    - Typically 60-80% smaller than full backup
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$NetBoxUrl,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ApiToken,

    [ValidateRange(1, 10000)]
    [int]$PageSize = 1000,

    [string]$OutputDirectory = "."
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ------------------------------------------------------------
# Basic configuration
# ------------------------------------------------------------

$NetBoxUrl = $NetBoxUrl.Trim().TrimEnd("/")

if ($NetBoxUrl -notmatch "^https://") {
    throw "NetBoxUrl must start with https://"
}

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item `
        -Path $OutputDirectory `
        -ItemType Directory `
        -Force | Out-Null
}

$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

$Headers = @{
    Authorization = "Token $ApiToken"
    Accept        = "application/json"
}

$TimeStamp = Get-Date -Format "yyyyMMdd_HHmmss"

$OutputFile = Join-Path `
    -Path $OutputDirectory `
    -ChildPath "NetBox_Minimal_Backup_$TimeStamp.json"

$TemporaryFile = "$OutputFile.tmp"

$LogFile = Join-Path `
    -Path $OutputDirectory `
    -ChildPath "NetBox_Minimal_Backup_$TimeStamp.log"

# ------------------------------------------------------------
# Minimum required endpoints for NetBox restore
# ------------------------------------------------------------

$MinimalEndpoints = @(
    # DCIM - Core infrastructure
    "dcim/regions",
    "dcim/site-groups",
    "dcim/sites",
    "dcim/locations",
    "dcim/rack-roles",
    "dcim/rack-groups",
    "dcim/rack-types",
    "dcim/racks",
    "dcim/manufacturers",
    "dcim/platforms",
    "dcim/device-roles",
    "dcim/device-types",
    "dcim/devices",
    "dcim/interfaces",
    "dcim/cables",
    "dcim/cable-terminations",
    "dcim/console-ports",
    "dcim/console-server-ports",
    "dcim/power-ports",
    "dcim/power-outlets",
    "dcim/power-panels",
    "dcim/power-feeds",
    "dcim/modules",
    "dcim/module-types",
    "dcim/module-bays",
    
    # IPAM - IP Address Management
    "ipam/rirs",
    "ipam/asn-ranges",
    "ipam/asns",
    "ipam/aggregates",
    "ipam/roles",
    "ipam/vrfs",
    "ipam/prefixes",
    "ipam/ip-ranges",
    "ipam/ip-addresses",
    "ipam/vlan-groups",
    "ipam/vlans",
    "ipam/service-templates",
    "ipam/services",
    "ipam/fhrp-groups",
    "ipam/fhrp-group-assignments",
    
    # Virtualization
    "virtualization/cluster-types",
    "virtualization/cluster-groups",
    "virtualization/clusters",
    "virtualization/virtual-machine-types",
    "virtualization/virtual-machines",
    "virtualization/interfaces",
    "virtualization/virtual-disks",
    
    # Tenancy
    "tenancy/tenant-groups",
    "tenancy/tenants",
    "tenancy/contact-groups",
    "tenancy/contact-roles",
    "tenancy/contacts",
    "tenancy/contact-assignments",
    
    # Circuits
    "circuits/providers",
    "circuits/provider-accounts",
    "circuits/provider-networks",
    "circuits/circuit-types",
    "circuits/circuits",
    "circuits/circuit-terminations",
    
    # Extras - Configuration only
    "extras/tags",
    "extras/custom-fields",
    "extras/custom-field-choice-sets",
    "extras/config-contexts",
    "extras/config-templates"
)

# ------------------------------------------------------------
# Storage
# ------------------------------------------------------------

$ExportedEndpoints = New-Object System.Collections.ArrayList
$BackupSummary = New-Object System.Collections.ArrayList

$BackupStarted = (Get-Date).ToString("o")

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [ValidateSet("INFO", "PASS", "WARN", "FAIL")]
        [string]$Level = "INFO"
    )

    $LogLine = "{0} [{1}] {2}" -f `
        (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), `
        $Level, `
        $Message

    Write-Host $LogLine

    Add-Content `
        -LiteralPath $LogFile `
        -Value $LogLine
}

# ------------------------------------------------------------
# API request
# ------------------------------------------------------------

function Invoke-NetBoxGet {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    Invoke-RestMethod `
        -Method Get `
        -Uri $Url `
        -Headers $Headers `
        -ErrorAction Stop
}

# ------------------------------------------------------------
# Add requested page size
# ------------------------------------------------------------

function Add-PageSize {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    if ($Url -match "[?&]limit=") {
        return $Url
    }

    if ($Url.Contains("?")) {
        return "$Url&limit=$PageSize"
    }

    return "$Url`?limit=$PageSize"
}

# ------------------------------------------------------------
# Export paginated endpoint
# ------------------------------------------------------------

function Get-NetBoxPaginatedEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EndpointUrl,

        [Parameter(Mandatory = $true)]
        [string]$EndpointPath
    )

    $Records = New-Object System.Collections.ArrayList

    $CurrentUrl = Add-PageSize -Url $EndpointUrl
    $ExpectedCount = $null
    $PageNumber = 0

    while ($null -ne $CurrentUrl -and $CurrentUrl -ne "") {
        $PageNumber++

        Write-Log `
            -Message ("Fetching {0}, page {1}" -f `
                $EndpointPath, `
                $PageNumber) `
            -Level "INFO"

        $Response = Invoke-NetBoxGet -Url $CurrentUrl

        $PropertyNames = @($Response.PSObject.Properties.Name)

        if ($PropertyNames -contains "count") {
            if ($null -eq $ExpectedCount) {
                $ExpectedCount = [int64]$Response.count
            }
        }

        if ($PropertyNames -contains "results") {
            foreach ($Record in @($Response.results)) {
                [void]$Records.Add($Record)
            }
        }
        else {
            throw "Endpoint did not return a paginated results property."
        }

        Write-Log `
            -Message ("Retrieved {0} of {1} records from {2}" -f `
                $Records.Count, `
                $ExpectedCount, `
                $EndpointPath) `
            -Level "INFO"

        if ($PropertyNames -contains "next") {
            $CurrentUrl = [string]$Response.next
        }
        else {
            $CurrentUrl = $null
        }
    }

    $DownloadedCount = $Records.Count

    if ($null -eq $ExpectedCount) {
        $Verified = $false
    }
    else {
        $Verified = ($DownloadedCount -eq $ExpectedCount)
    }

    [pscustomobject]@{
        EndpointPath    = $EndpointPath
        EndpointUrl     = $EndpointUrl
        ExpectedCount   = $ExpectedCount
        DownloadedCount = $DownloadedCount
        Pages           = $PageNumber
        Verified        = $Verified
        Records         = @($Records)
    }
}

# ------------------------------------------------------------
# Start
# ------------------------------------------------------------

Write-Host ""
Write-Host "=============================================="
Write-Host " NETBOX MINIMAL BACKUP V3.1.1"
Write-Host "=============================================="
Write-Host ""

# ------------------------------------------------------------
# Test connection
# ------------------------------------------------------------

try {
    Write-Log `
        -Message "Testing NetBox API connection" `
        -Level "INFO"

    $StatusUrl = "$NetBoxUrl/api/status/"
    $Status = Invoke-NetBoxGet -Url $StatusUrl

    $NetBoxVersion = [string]$Status.'netbox-version'

    Write-Log `
        -Message ("Connected to NetBox version {0}" -f $NetBoxVersion) `
        -Level "PASS"
}
catch {
    Write-Log `
        -Message ("Unable to connect to NetBox: {0}" -f `
            $_.Exception.Message) `
        -Level "FAIL"

    exit 1
}

# ------------------------------------------------------------
# Export minimal endpoints
# ------------------------------------------------------------

$SuccessfulCount = 0
$FailedCount = 0

foreach ($EndpointPath in $MinimalEndpoints) {
    $EndpointUrl = "$NetBoxUrl/api/$EndpointPath/"
    
    Write-Host ""
    Write-Host ("Processing: {0}" -f $EndpointPath)
    
    try {
        $Result = Get-NetBoxPaginatedEndpoint `
            -EndpointUrl $EndpointUrl `
            -EndpointPath $EndpointPath

        [void]$ExportedEndpoints.Add(
            [pscustomobject]@{
                path             = $Result.EndpointPath
                url              = $Result.EndpointUrl
                expected_count   = $Result.ExpectedCount
                downloaded_count = $Result.DownloadedCount
                pages            = $Result.Pages
                verified         = $Result.Verified
                records          = $Result.Records
            }
        )

        if ($Result.Verified) {
            $ResultText = "PASS"
            $LogLevel = "PASS"
            $SuccessfulCount++
        }
        else {
            $ResultText = "COUNT MISMATCH"
            $LogLevel = "FAIL"
            $FailedCount++
        }

        [void]$BackupSummary.Add(
            [pscustomobject]@{
                path             = $EndpointPath
                url              = $EndpointUrl
                expected_count   = $Result.ExpectedCount
                downloaded_count = $Result.DownloadedCount
                pages            = $Result.Pages
                result           = $ResultText
                error            = ""
            }
        )

        Write-Log `
            -Message ("{0} {1}: {2} of {3} records across {4} page(s)" -f `
                $ResultText, `
                $EndpointPath, `
                $Result.DownloadedCount, `
                $Result.ExpectedCount, `
                $Result.Pages) `
            -Level $LogLevel
    }
    catch {
        $FailedCount++
        
        [void]$BackupSummary.Add(
            [pscustomobject]@{
                path             = $EndpointPath
                url              = $EndpointUrl
                expected_count   = $null
                downloaded_count = 0
                pages            = 0
                result           = "FAILED"
                error            = $_.Exception.Message
            }
        )

        Write-Log `
            -Message ("FAILED {0}: {1}" -f $EndpointPath, $_.Exception.Message) `
            -Level "FAIL"
    }
}

# ------------------------------------------------------------
# Prepare backup data
# ------------------------------------------------------------

$BackupCompleted = (Get-Date).ToString("o")

$BackupData = [ordered]@{
    metadata = [ordered]@{
        backup_started       = $BackupStarted
        backup_completed     = $BackupCompleted
        netbox_url           = $NetBoxUrl
        netbox_version       = $NetBoxVersion
        requested_page_size  = $PageSize
        endpoints_processed  = $BackupSummary.Count
        successful_endpoints = $SuccessfulCount
        failed_endpoints     = $FailedCount
        script_version       = "3.1.1-minimal"
        backup_type          = "minimal"
        description          = "Minimal NetBox backup containing only essential data for restore"
    }

    endpoints = @($ExportedEndpoints)
    summary   = @($BackupSummary)
}

# ------------------------------------------------------------
# Write and validate JSON
# ------------------------------------------------------------

Write-Host ""

Write-Log `
    -Message "Writing minimal JSON backup" `
    -Level "INFO"

try {
    $BackupData |
        ConvertTo-Json -Depth 100 |
        Set-Content `
            -LiteralPath $TemporaryFile `
            -Encoding UTF8

    $JsonText = Get-Content `
        -LiteralPath $TemporaryFile `
        -Raw

    $null = $JsonText | ConvertFrom-Json

    Move-Item `
        -LiteralPath $TemporaryFile `
        -Destination $OutputFile `
        -Force

    Write-Log `
        -Message "JSON validation passed" `
        -Level "PASS"
}
catch {
    if (Test-Path -LiteralPath $TemporaryFile) {
        Remove-Item `
            -LiteralPath $TemporaryFile `
            -Force
    }

    Write-Log `
        -Message ("Failed to write or validate JSON: {0}" -f `
            $_.Exception.Message) `
        -Level "FAIL"

    exit 1
}

# ------------------------------------------------------------
# File size
# ------------------------------------------------------------

$OutputFileInformation = Get-Item -LiteralPath $OutputFile

$FileSizeMB = "{0:N2}" -f (
    $OutputFileInformation.Length / 1MB
)

# ------------------------------------------------------------
# Display summary
# ------------------------------------------------------------

Write-Host ""
Write-Host "=============================================="
Write-Host " MINIMAL BACKUP SUMMARY"
Write-Host "=============================================="
Write-Host ""

$BackupSummary |
    Select-Object `
        path,
        expected_count,
        downloaded_count,
        pages,
        result |
    Format-Table -AutoSize

Write-Host ""
Write-Host ("Backup file       : {0}" -f $OutputFile)
Write-Host ("Backup size       : {0} MB" -f $FileSizeMB)
Write-Host ("Log file          : {0}" -f $LogFile)
Write-Host ("NetBox version    : {0}" -f $NetBoxVersion)
Write-Host ("Endpoints checked : {0}" -f $BackupSummary.Count)
Write-Host ("Successful        : {0}" -f $SuccessfulCount)
Write-Host ("Failed/mismatched : {0}" -f $FailedCount)
Write-Host ""
Write-Host "Minimal backup excludes:"
Write-Host "  - Audit logs (object-changes)"
Write-Host "  - Background jobs and tasks"
Write-Host "  - User accounts and permissions"
Write-Host "  - Bookmarks and UI preferences"
Write-Host "  - Plugin-specific data"
Write-Host ""

if ($FailedCount -gt 0) {
    Write-Warning "Backup completed with failures or count mismatches."
    Write-Warning "Check the summary and log before treating it as complete."
    exit 2
}

Write-Host "MINIMAL BACKUP COMPLETED SUCCESSFULLY"
exit 0
