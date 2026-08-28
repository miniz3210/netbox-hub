<#
.SYNOPSIS
    NetBox-to-Cloud Hub Sync Agent (production-ready v3.0.1)
    Auto-paginates NetBox, env-var secrets, robust error handling.

.DESCRIPTION
    Pulls sites, VLANs, prefixes, devices, and virtual-machines from an internal
    NetBox instance and pushes them to a NetBox Universal Library Hub running
    the REST ingest endpoint at /api/v1/sync/push.

    Compatible with Windows PowerShell 5.1 and PowerShell 7+.

.PARAMETER NetBoxUrl
    Base URL of the source NetBox instance (e.g. https://ipam.example.com).
    Use http:// if your NetBox does not have TLS (e.g. internal AD zones).

.PARAMETER NetBoxToken
    NetBox API token. Read from $env:NETBOX_TOKEN if not specified.

.PARAMETER HubEndpoint
    Full URL of the Hub sync ingest endpoint.

.PARAMETER HubSyncKey
    Shared secret for the Hub. Read from $env:HUB_SYNC_KEY if not specified;
    falls back to the well-known dev key "netbox-hub-secret-sync-key".

.PARAMETER SkipSslCheck
    Bypass TLS certificate validation (lab / self-signed / inspection appliances).

.PARAMETER PageSize
    NetBox pagination page size (default 1000 — NetBox maximum).

.PARAMETER TimeoutSec
    Per-request timeout in seconds (default 60).

.PARAMETER Compress
    Send the payload gzip-compressed with Content-Encoding: gzip. The matching
    Hub handler must be patched to decompress; OFF by default for compatibility.

.PARAMETER WhatIf
    Run all NetBox queries but DO NOT push to the Hub.

.EXAMPLE
    pwsh ./sync-agent.ps1 `
        -NetBoxUrl "https://ipam.aw.ads" `
        -NetBoxToken $env:NETBOX_TOKEN `
        -HubEndpoint "https://netbox-hub.lovelyndha.pp.ua/api/v1/sync/push"

.EXAMPLE
    # Dry run, no push
    pwsh ./sync-agent.ps1 -NetBoxUrl "http://ipam.aw.ads" -NetBoxToken $env:NETBOX_TOKEN -WhatIf

.NOTES
    Author : NetBox Hub maintainers
    Version: 3.0.1
#>

[CmdletBinding()]
param (
    [string]$NetBoxUrl   = "https://ipam.aw.ads",
    [string]$NetBoxToken = $(if ($env:NETBOX_TOKEN) { $env:NETBOX_TOKEN } else { "" }),
    [string]$HubEndpoint = "https://netbox-hub.lovelyndha.pp.ua/api/v1/sync/push",
    [string]$HubSyncKey  = $(if ($env:HUB_SYNC_KEY)  { $env:HUB_SYNC_KEY  } else { "netbox-hub-secret-sync-key" }),
    [switch]$SkipSslCheck = $true,
    [ValidateRange(1, 1000)]
    [int]$PageSize       = 1000,
    [ValidateRange(5, 600)]
    [int]$TimeoutSec     = 60,
    [switch]$Compress    = $false,   # OFF by default: the Hub handler decodes
                                      # UTF-8 directly and will reject gzip bodies.
    [switch]$WhatIf      = $false
)

# ─── Pre-flight validation ─────────────────────────────────────────────────
# Re-pull env-var fallbacks here in case the user didn't pass them on the CLI
if ([string]::IsNullOrWhiteSpace($HubSyncKey) -and -not [string]::IsNullOrWhiteSpace($env:HUB_SYNC_KEY)) {
    $HubSyncKey = $env:HUB_SYNC_KEY
}
if ([string]::IsNullOrWhiteSpace($NetBoxToken) -and -not [string]::IsNullOrWhiteSpace($env:NETBOX_TOKEN)) {
    $NetBoxToken = $env:NETBOX_TOKEN
}

if ([string]::IsNullOrWhiteSpace($NetBoxToken)) {
    $envName = 'NETBOX_TOKEN'
    $msg = "NetBoxToken is required. Supply it via -NetBoxToken or set environment variable: $envName"
    Write-Host "`n[FATAL] $msg" -ForegroundColor Red
    throw $msg
}
if ([string]::IsNullOrWhiteSpace($HubSyncKey)) {
    $envName = 'HUB_SYNC_KEY'
    $msg = "HubSyncKey is required. Supply it via -HubSyncKey or set environment variable: $envName"
    Write-Host "`n[FATAL] $msg" -ForegroundColor Red
    throw $msg
}
if (-not $HubEndpoint.StartsWith("http://") -and -not $HubEndpoint.StartsWith("https://")) {
    throw "HubEndpoint must be a full URL (https://...). Got: $HubEndpoint"
}

# ─── 1. TLS / SSL bypass (lab environments only) ───────────────────────────
if ($SkipSslCheck) {
    Write-Host "[SSL] TLS validation disabled (lab mode)." -ForegroundColor DarkYellow
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { param($s,$c,$ch,$e) $true }
    } else {
        if (-not ([System.Management.Automation.PSTypeName]'TrustAllCertsPolicy').Type) {
            $trustAll = @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class TrustAllCertsPolicy : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint sp, X509Certificate cert, WebRequest req, int problem) { return true; }
}
"@
            Add-Type -TypeDefinition $trustAll
        }
        [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { param($s,$c,$ch,$e) $true }
    }
}
# Negotiate TLS 1.2 + 1.1 + 1.0; 1.3 is the OS default in PS 7+ on Win11/Win2022
[System.Net.ServicePointManager]::SecurityProtocol = `
    [System.Net.SecurityProtocolType]::Tls12 -bor `
    [System.Net.SecurityProtocolType]::Tls11 -bor `
    [System.Net.SecurityProtocolType]::Tls

# ─── 2. Banner ─────────────────────────────────────────────────────────────
$banner = @"

==================================================
⚡ NetBox Live Sync Agent -> Cloud Hub  (v3.0.1)
==================================================
  Source   : $NetBoxUrl
  Hub      : $HubEndpoint
  Compress : $Compress
  WhatIf   : $WhatIf
==================================================
"@
Write-Host $banner -ForegroundColor Cyan

# ─── 3. Source headers (NetBox) ────────────────────────────────────────────
$nbHeaders = @{
    "Authorization" = "Token $NetBoxToken"
    "Accept"        = "application/json"
}

# ─── 4. Paginated NetBox fetch ─────────────────────────────────────────────
function Get-NetBoxData {
    [CmdletBinding()]
    param ([Parameter(Mandatory)][string]$Endpoint)

    $baseUrl = "$($NetBoxUrl.TrimEnd('/'))/api/$($Endpoint.TrimStart('/'))"
    $url     = "$baseUrl`?limit=$PageSize"
    $all     = New-Object System.Collections.Generic.List[object]
    $page    = 0

    while ($url) {
        $page++
        try {
            Write-Host ("  -> {0,-38} page {1}..." -f $Endpoint, $page) -NoNewline
            $resp = Invoke-RestMethod -Uri $url -Headers $nbHeaders -Method Get -TimeoutSec $TimeoutSec
            $batch = 0
            if ($resp.results) {
                $batch = @($resp.results).Count
                foreach ($r in $resp.results) { $all.Add($r) }
            }
            Write-Host (" [OK +{0} (total {1})]" -f $batch, $all.Count) -ForegroundColor Green
            $url = $resp.next
        } catch {
            Write-Host (" [FAILED: {0}]" -f $_.Exception.Message) -ForegroundColor Red
            return $all.ToArray()
        }
    }
    return $all.ToArray()
}

# ─── 5. Pull live data from internal NetBox ────────────────────────────────
Write-Host "`n[1/2] Querying Internal NetBox API..." -ForegroundColor Yellow
$sites    = Get-NetBoxData -Endpoint "dcim/sites/"
$vlans    = Get-NetBoxData -Endpoint "ipam/vlans/"
$prefixes = Get-NetBoxData -Endpoint "ipam/prefixes/"
$devices  = Get-NetBoxData -Endpoint "dcim/devices/"
$vms      = Get-NetBoxData -Endpoint "virtualization/virtual-machines/"

Write-Host ""
Write-Host ("  Summary : {0} sites, {1} vlans, {2} prefixes, {3} devices, {4} vms" -f `
    $sites.Count, $vlans.Count, $prefixes.Count, $devices.Count, $vms.Count) -ForegroundColor White

# ─── 6. Build the payload (handles empty arrays correctly) ─────────────────
$payloadObj = [ordered]@{
    sync_key = $HubSyncKey
    sites    = @($sites)
    vlans    = @($vlans)
    prefixes = @($prefixes)
    devices  = @($devices)
    vms      = @($vms)
}
$payloadJson = $payloadObj | ConvertTo-Json -Depth 12 -Compress
Write-Host ("  Payload : {0:N1} MB (raw JSON)" -f ($payloadJson.Length / 1MB)) -ForegroundColor Gray

if ($WhatIf) {
    Write-Host "`n[WhatIf] Skipping push to Hub." -ForegroundColor Yellow
    Write-Host "==================================================" -ForegroundColor Cyan
    return
}

# ─── 7. Push to the Hub (with optional gzip) ───────────────────────────────
$hubHeaders = @{
    "Content-Type" = "application/json"
    "X-Hub-Key"    = $HubSyncKey
}

$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($payloadJson)
if ($Compress) {
    $mem = New-Object System.IO.MemoryStream
    $gz  = New-Object System.IO.Compression.GzipStream($mem, [System.IO.Compression.CompressionMode]::Compress)
    $gz.Write($bodyBytes, 0, $bodyBytes.Length)
    $gz.Close()
    $bodyBytes   = $mem.ToArray()
    $hubHeaders["Content-Encoding"] = "gzip"
    Write-Host ("  Compressed to {0:N1} MB (gzip)" -f ($bodyBytes.Length / 1MB)) -ForegroundColor Gray
}

# Tip for testing the Hub from the shell:
#   PowerShell aliases "curl" to Invoke-WebRequest (different syntax).
#   Use the real curl from /usr/bin or a manual install with "curl.exe":
#
#     curl.exe -s -o NUL -w "GET  %{http_code}`n"  https://netbox-hub.lovelyndha.pp.ua/api/v1/sync/push
#     curl.exe -s -X POST -H "Content-Type: application/json" -H "X-Hub-Key: netbox-hub-secret-sync-key" -d "{}" https://netbox-hub.lovelyndha.pp.ua/api/v1/sync/push

Write-Host "`n[2/2] Pushing data to Cloud Hub: $HubEndpoint..." -ForegroundColor Yellow
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $resp = Invoke-WebRequest -Uri $HubEndpoint -Method Post -Headers $hubHeaders -Body $bodyBytes -TimeoutSec $TimeoutSec
    $sw.Stop()
    $body = $null
    try { $body = $resp.Content | ConvertFrom-Json -ErrorAction Stop } catch {}

    if ($null -ne $body -and $body.success) {
        Write-Host "`n✅ SYNC SUCCESSFUL in $($sw.Elapsed.TotalSeconds.ToString('0.0'))s" -ForegroundColor Green
        Write-Host "   • Sites Imported:    $($body.imported.sites)" -ForegroundColor White
        Write-Host "   • Prefixes Imported: $($body.imported.prefixes)" -ForegroundColor White
        Write-Host "   • Devices Imported:  $($body.imported.devices)" -ForegroundColor White
        Write-Host "   • VMs Imported:      $($body.imported.vms)" -ForegroundColor White
    } elseif ($null -ne $body -and $body.error) {
        Write-Host "`n❌ Hub returned error: $($body.error)" -ForegroundColor Red
        if ($body.detail) { Write-Host "   Detail: $($body.detail)" -ForegroundColor Red }
    } else {
        Write-Host "`n⚠️  Unexpected response (HTTP $($resp.StatusCode)):" -ForegroundColor Yellow
        Write-Host $resp.Content.Substring(0, [Math]::Min(400, $resp.Content.Length)) -ForegroundColor Yellow
        exit 2
    }
} catch {
    $sw.Stop()
    $statusCode = $null
    $body       = $null
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
        try {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $body   = $reader.ReadToEnd()
        } catch {}
    }
    Write-Host "`n❌ Push failed after $($sw.Elapsed.TotalSeconds.ToString('0.0'))s : $($_.Exception.Message)" -ForegroundColor Red
    if ($statusCode) { Write-Host "   HTTP $statusCode" -ForegroundColor Red }
    if ($body)       { Write-Host "   $body" -ForegroundColor Red }
    exit 1
}

Write-Host "`n==================================================" -ForegroundColor Cyan
