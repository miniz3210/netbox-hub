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
    -ChildPath "NetBox_Full_Backup_$TimeStamp.json"

$TemporaryFile = "$OutputFile.tmp"

$LogFile = Join-Path `
    -Path $OutputDirectory `
    -ChildPath "NetBox_Full_Backup_$TimeStamp.log"

# ------------------------------------------------------------
# Storage
# ------------------------------------------------------------

$ProcessedUrls = @{}

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
# Store a failed endpoint
# ------------------------------------------------------------

function Add-FailedEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [string]$ErrorMessage
    )

    [void]$BackupSummary.Add(
        [pscustomobject]@{
            path             = $Path
            url              = $Url
            expected_count   = $null
            downloaded_count = 0
            pages            = 0
            result           = "FAILED"
            error            = $ErrorMessage
        }
    )

    Write-Log `
        -Message ("FAILED {0}: {1}" -f $Path, $ErrorMessage) `
        -Level "FAIL"
}

# ------------------------------------------------------------
# Recursively discover and export endpoints
# ------------------------------------------------------------

function Export-NetBoxUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [int]$Depth = 0
    )

    # Prevent an unexpected recursive loop.
    if ($Depth -gt 10) {
        Add-FailedEndpoint `
            -Path $Path `
            -Url $Url `
            -ErrorMessage "Maximum discovery depth exceeded."

        return
    }

    # Avoid exporting the same URL twice.
    if ($ProcessedUrls.ContainsKey($Url)) {
        Write-Log `
            -Message ("Skipping duplicate URL for {0}" -f $Path) `
            -Level "WARN"

        return
    }

    $ProcessedUrls[$Url] = $true

    Write-Log `
        -Message ("Discovering {0}" -f $Path) `
        -Level "INFO"

    try {
        $Response = Invoke-NetBoxGet -Url $Url
    }
    catch {
        Add-FailedEndpoint `
            -Path $Path `
            -Url $Url `
            -ErrorMessage $_.Exception.Message

        return
    }

    $PropertyNames = @($Response.PSObject.Properties.Name)

    # --------------------------------------------------------
    # Paginated list endpoint
    # --------------------------------------------------------

    if ($PropertyNames -contains "results") {
        try {
            $Result = Get-NetBoxPaginatedEndpoint `
                -EndpointUrl $Url `
                -EndpointPath $Path

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
            }
            else {
                $ResultText = "COUNT MISMATCH"
                $LogLevel = "FAIL"
            }

            [void]$BackupSummary.Add(
                [pscustomobject]@{
                    path             = $Path
                    url              = $Url
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
                    $Path, `
                    $Result.DownloadedCount, `
                    $Result.ExpectedCount, `
                    $Result.Pages) `
                -Level $LogLevel
        }
        catch {
            Add-FailedEndpoint `
                -Path $Path `
                -Url $Url `
                -ErrorMessage $_.Exception.Message
        }

        return
    }

    # --------------------------------------------------------
    # Directory endpoint
    # --------------------------------------------------------

    $ChildEndpoints = New-Object System.Collections.ArrayList

    foreach ($Property in $Response.PSObject.Properties) {
        $PropertyName = [string]$Property.Name
        $PropertyValue = $Property.Value

        if (
            $PropertyValue -is [string] -and
            $PropertyValue -match "^https?://"
        ) {
            [void]$ChildEndpoints.Add(
                [pscustomobject]@{
                    Name = $PropertyName
                    Url  = [string]$PropertyValue
                }
            )
        }
    }

    if ($ChildEndpoints.Count -gt 0) {
        foreach ($Child in $ChildEndpoints) {
            $ChildPath = "{0}/{1}" -f $Path, $Child.Name

            Export-NetBoxUrl `
                -Url $Child.Url `
                -Path $ChildPath `
                -Depth ($Depth + 1)
        }

        return
    }

    # --------------------------------------------------------
    # Non-paginated endpoint, such as status
    # --------------------------------------------------------

    [void]$ExportedEndpoints.Add(
        [pscustomobject]@{
            path             = $Path
            url              = $Url
            expected_count   = 1
            downloaded_count = 1
            pages            = 1
            verified         = $true
            records          = @($Response)
        }
    )

    [void]$BackupSummary.Add(
        [pscustomobject]@{
            path             = $Path
            url              = $Url
            expected_count   = 1
            downloaded_count = 1
            pages            = 1
            result           = "PASS"
            error            = ""
        }
    )

    Write-Log `
        -Message ("PASS {0}: non-paginated API response captured" -f $Path) `
        -Level "PASS"
}

# ------------------------------------------------------------
# Start
# ------------------------------------------------------------

Write-Host ""
Write-Host "=============================================="
Write-Host " NETBOX COMPLETE REST API BACKUP V2"
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
# Obtain API root
# ------------------------------------------------------------

try {
    $ApiRootUrl = "$NetBoxUrl/api/"
    $ApiRoot = Invoke-NetBoxGet -Url $ApiRootUrl

    Write-Log `
        -Message "NetBox API root retrieved successfully" `
        -Level "PASS"
}
catch {
    Write-Log `
        -Message ("Unable to retrieve the API root: {0}" -f `
            $_.Exception.Message) `
        -Level "FAIL"

    exit 1
}

# ------------------------------------------------------------
# Discover sections
# ------------------------------------------------------------

foreach ($Section in $ApiRoot.PSObject.Properties) {
    $SectionName = [string]$Section.Name
    $SectionUrl = $Section.Value

    if ($SectionUrl -isnot [string]) {
        Write-Log `
            -Message ("Skipping non-URL API property {0}" -f `
                $SectionName) `
            -Level "WARN"

        continue
    }

    if ($SectionUrl -notmatch "^https?://") {
        Write-Log `
            -Message ("Skipping invalid API URL for {0}" -f `
                $SectionName) `
            -Level "WARN"

        continue
    }

    Write-Host ""
    Write-Host ("SECTION: {0}" -f $SectionName)
    Write-Host ""

    Export-NetBoxUrl `
        -Url $SectionUrl `
        -Path $SectionName `
        -Depth 0
}

# ------------------------------------------------------------
# Calculate results
# ------------------------------------------------------------

$SuccessfulCount = @(
    $BackupSummary |
        Where-Object {
            $_.result -eq "PASS"
        }
).Count

$FailedCount = @(
    $BackupSummary |
        Where-Object {
            $_.result -eq "FAILED" -or
            $_.result -eq "COUNT MISMATCH"
        }
).Count

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
        script_version       = "2.0"
    }

    endpoints = @($ExportedEndpoints)
    summary   = @($BackupSummary)
}

# ------------------------------------------------------------
# Write and validate JSON
# ------------------------------------------------------------

Write-Host ""

Write-Log `
    -Message "Writing complete JSON backup" `
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
Write-Host " BACKUP SUMMARY"
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

if ($FailedCount -gt 0) {
    Write-Warning "Backup completed with failures or count mismatches."
    Write-Warning "Check the summary and log before treating it as complete."
    exit 2
}

Write-Host "COMPLETE REST API BACKUP PASSED"
exit 0
