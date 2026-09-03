param(
    [Parameter(Mandatory = $true)]
    [string]$NetBoxUrl,

    [Parameter(Mandatory = $true)]
    [string]$ApiToken,

    [int]$PageSize = 2000
)

# Remove trailing slash
$NetBoxUrl = $NetBoxUrl.TrimEnd('/')

# API headers
$Headers = @{
    Authorization = "Token $ApiToken"
    Accept        = "application/json"
}

# Output file
$TimeStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutputFile = "NetBox_Backup_$TimeStamp.json"

# Storage object
$BackupData = [ordered]@{}

function Get-PaginatedData {
    param(
        [string]$Endpoint
    )

    $Results = @()
    $Url = "$NetBoxUrl/api/$Endpoint/?limit=$PageSize"

    do {

        Write-Host "Fetching: $Url"

        $Response = Invoke-RestMethod `
            -Uri $Url `
            -Method GET `
            -Headers $Headers `
            -ErrorAction Stop

        if ($Response.results) {

            $Results += $Response.results

            Write-Host ("Retrieved {0} records" -f $Results.Count)

            $Url = $Response.next
        }
        else {

            $Url = $null
        }

    } while ($Url)

    return $Results
}

# Test API connection
try {

    $Status = Invoke-RestMethod `
        -Uri "$NetBoxUrl/api/status/" `
        -Method GET `
        -Headers $Headers `
        -ErrorAction Stop

    Write-Host ""
    Write-Host "Connected to NetBox"
    Write-Host ("NetBox Version: {0}" -f $Status.'netbox-version')
    Write-Host ""
}
catch {

    Write-Error "Unable to connect to NetBox API"
    exit 1
}

# Endpoints to export
$Endpoints = @(
    "dcim/sites",
    "dcim/regions",
    "dcim/racks",
    "dcim/manufacturers",
    "dcim/device-types",
    "dcim/device-roles",
    "dcim/platforms",
    "dcim/devices",
    "dcim/interfaces",

    "ipam/vrfs",
    "ipam/vlans",
    "ipam/prefixes",
    "ipam/ip-addresses",

    "virtualization/clusters",
    "virtualization/virtual-machines",

    "tenancy/tenants",

    "circuits/providers",
    "circuits/circuits"
)

Write-Host "====================================="
Write-Host "STARTING NETBOX BACKUP"
Write-Host "====================================="

foreach ($Endpoint in $Endpoints) {

    Write-Host ""
    Write-Host "Exporting $Endpoint"

    try {

        $Key = $Endpoint.Replace("/", "_")

        $Data = Get-PaginatedData -Endpoint $Endpoint

        $BackupData[$Key] = $Data

        Write-Host ("Completed: {0} ({1} records)" -f $Endpoint, $Data.Count)
    }
    catch {

        Write-Warning ("Failed: {0}" -f $Endpoint)
        Write-Warning $_.Exception.Message
    }
}

Write-Host ""
Write-Host "Writing JSON backup..."

$BackupData |
    ConvertTo-Json -Depth 100 |
    Set-Content -Path $OutputFile -Encoding UTF8

Write-Host ""
Write-Host "====================================="
Write-Host "BACKUP COMPLETED"
Write-Host "====================================="
Write-Host "File : $OutputFile"
Write-Host ""