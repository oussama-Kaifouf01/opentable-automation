[CmdletBinding()]
param(
    [string]$InstallDir = "C:\OpenTableAutomation",
    [string]$JobsUrl,
    [string]$StatusUrl,
    [ValidateSet("POST", "PUT")]
    [string]$StatusMethod = "PUT",
    [string]$DaemonUrl = "http://127.0.0.1:8765",
    [string]$ReservationsUrl,
    [int]$TimeoutMs = 60000,
    [switch]$SkipSetup,
    [switch]$RegisterStartup,
    [switch]$StartNow,
    [switch]$OverwriteConfig
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Copy-ProjectFiles {
    param([string]$Source, [string]$Destination)

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $excludedDirs = @(".git", ".venv", ".opentable-profile", ".opentable-profile-camoufox", ".opentable-profile-chromium", ".opentable-profile-test", "artifacts", "__pycache__", "delivery")
    $excludedFiles = @(".env", "config.json", "*.pyc", "*.pyo", "*.log")

    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        $skip = $false
        if ($item.PSIsContainer -and $excludedDirs -contains $item.Name) {
            $skip = $true
        }
        if (-not $skip) {
            foreach ($pattern in $excludedFiles) {
                if ($item.Name -like $pattern) {
                    $skip = $true
                    break
                }
            }
        }
        if ($skip) {
            continue
        }
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }

    Get-ChildItem -LiteralPath $Destination -Recurse -Force -Directory |
        Where-Object { $_.Name -eq "__pycache__" } |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $Destination -Recurse -Force -File -Include "*.pyc", "*.pyo" |
        Remove-Item -Force

    if ($OverwriteConfig -or -not (Test-Path -LiteralPath (Join-Path $Destination "config.json"))) {
        Copy-Item -LiteralPath (Join-Path $Source "config.example.json") -Destination (Join-Path $Destination "config.json") -Force
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Destination ".env"))) {
        Copy-Item -LiteralPath (Join-Path $Source ".env.example") -Destination (Join-Path $Destination ".env") -Force
    }
}

function Update-Config {
    param([string]$Path)

    $config = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $config.browser.timeout_ms = $TimeoutMs
    if ($ReservationsUrl) {
        $config.admin.reservations_url = $ReservationsUrl
    }
    $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding ASCII
}

Write-Step "Installing files"
$sourceFull = [System.IO.Path]::GetFullPath($SourceDir).TrimEnd('\')
$installFull = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ($sourceFull.Equals($installFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "Installer is already running from the install directory."
    if ($OverwriteConfig -or -not (Test-Path -LiteralPath (Join-Path $InstallDir "config.json"))) {
        Copy-Item -LiteralPath (Join-Path $InstallDir "config.example.json") -Destination (Join-Path $InstallDir "config.json") -Force
    }
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir ".env"))) {
        Copy-Item -LiteralPath (Join-Path $InstallDir ".env.example") -Destination (Join-Path $InstallDir ".env") -Force
    }
} else {
    Copy-ProjectFiles -Source $SourceDir -Destination $InstallDir
}
Set-Location -LiteralPath $InstallDir

Write-Step "Updating config"
Update-Config -Path (Join-Path $InstallDir "config.json")

if (-not $SkipSetup) {
    Write-Step "Running Windows setup"
    & (Join-Path $InstallDir "setup-windows.ps1")
}

if ($RegisterStartup) {
    if (-not $JobsUrl) {
        throw "-JobsUrl is required when using -RegisterStartup."
    }
    Write-Step "Registering startup tasks"
    $registerArgs = @(
        "-JobsUrl", $JobsUrl,
        "-StatusMethod", $StatusMethod,
        "-DaemonUrl", $DaemonUrl
    )
    if ($StatusUrl) {
        $registerArgs += @("-StatusUrl", $StatusUrl)
    }
    & (Join-Path $InstallDir "register-startup-tasks.ps1") @registerArgs
}

Write-Step "Running health check"
$healthArgs = @("-DaemonUrl", $DaemonUrl)
if ($JobsUrl) {
    $healthArgs += @("-JobsUrl", $JobsUrl)
}
if ($StatusUrl) {
    $healthArgs += @("-StatusUrl", $StatusUrl)
}
& (Join-Path $InstallDir "check-installation.ps1") @healthArgs

if ($StartNow) {
    if (-not $JobsUrl) {
        throw "-JobsUrl is required when using -StartNow."
    }
    Write-Step "Starting automation"
    $startArgs = @(
        "-JobsUrl", $JobsUrl,
        "-StatusMethod", $StatusMethod,
        "-DaemonUrl", $DaemonUrl
    )
    if ($StatusUrl) {
        $startArgs += @("-StatusUrl", $StatusUrl)
    }
    & (Join-Path $InstallDir "start-all.ps1") @startArgs
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Installed at: $InstallDir"
Write-Host "Next: run '.\.venv\Scripts\python.exe run.py login' from $InstallDir and complete GuestCenter login."
