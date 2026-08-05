[CmdletBinding()]
param(
    [string]$PythonCommand = "python",
    [switch]$SkipPythonInstall,
    [switch]$SkipChromium
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Step "Checking Python"
$PythonWorks = $false
if (Test-Command $PythonCommand) {
    try {
        & $PythonCommand --version | Out-Null
        $PythonWorks = $LASTEXITCODE -eq 0
    } catch {
        $PythonWorks = $false
    }
}

if (-not $PythonWorks) {
    if ($SkipPythonInstall) {
        throw "Python was not found. Install Python 3.11 or run without -SkipPythonInstall."
    }
    if (-not (Test-Command "winget")) {
        throw "Python and winget were not found. Install Python 3.11 from https://www.python.org/downloads/windows/ and run this script again."
    }

    winget install --id Python.Python.3.11 --exact --accept-package-agreements --accept-source-agreements
    if (Test-Command "py") {
        $PythonCommand = "py"
    } else {
        $InstalledPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
        if (-not (Test-Path -LiteralPath $InstalledPython)) {
            throw "Python was installed, but this terminal cannot locate it. Open a new PowerShell window and run this script again."
        }
        $PythonCommand = $InstalledPython
    }
}

if ($PythonCommand -eq "py") {
    $PythonArgs = @("-3.11")
} else {
    $PythonArgs = @()
}

if (Test-Command "winget") {
    Write-Step "Installing Microsoft Visual C++ runtime"
    winget install `
        --id Microsoft.VCRedist.2015+.x64 `
        --exact `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements
}

Write-Step "Creating Python virtual environment"
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $PythonCommand @PythonArgs -m venv .venv
}
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

Write-Step "Installing Python packages"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt
& $VenvPython -m pip install --upgrade --force-reinstall --no-cache-dir greenlet

if (-not $SkipChromium) {
    Write-Step "Installing Playwright Chromium fallback"
    & $VenvPython -m playwright install chromium
}

Write-Step "Downloading Camoufox"
& $VenvPython -m camoufox fetch

Write-Step "Applying the Playwright driver compatibility patch"
& $VenvPython scripts\patch_playwright_driver.py

Write-Step "Initializing local configuration"
if (-not (Test-Path -LiteralPath "config.json")) {
    Copy-Item -LiteralPath "config.example.json" -Destination "config.json"
    Write-Host "Created config.json from config.example.json."
} else {
    Write-Host "Kept existing config.json."
}

if (-not (Test-Path -LiteralPath ".env")) {
    @(
        "OPENTABLE_EMAIL="
        "OPENTABLE_PASSWORD="
    ) | Set-Content -LiteralPath ".env" -Encoding ASCII
    Write-Host "Created an empty .env file."
} else {
    Write-Host "Kept existing .env."
}

New-Item -ItemType Directory -Path "artifacts" -Force | Out-Null
New-Item -ItemType Directory -Path ".opentable-profile-camoufox" -Force | Out-Null

Write-Step "Validating installation"
& $VenvPython -c "import struct, greenlet, camoufox, playwright, dotenv; assert struct.calcsize('P') * 8 == 64, '64-bit Python is required'; print('Python dependencies and native DLLs: OK')"
& $VenvPython run.py --help | Out-Null

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "1. Review config.json, especially admin.reservations_url."
Write-Host "2. Run .\start-daemon.ps1 and log into GuestCenter in Camoufox."
Write-Host "3. Run .\start-poller.ps1 in another PowerShell window."
Write-Host "4. The browser profile is stored in .opentable-profile-camoufox."
