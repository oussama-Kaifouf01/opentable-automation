[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
Set-Location -LiteralPath $ProjectDir

if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Installing Microsoft Visual C++ runtime..." -ForegroundColor Cyan
    winget install `
        --id Microsoft.VCRedist.2015+.x64 `
        --exact `
        --silent `
        --accept-package-agreements `
        --accept-source-agreements
} else {
    Write-Warning "winget is unavailable. Install the Microsoft Visual C++ 2015-2022 x64 Redistributable manually."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "The virtual environment does not exist. Run .\setup-windows.ps1 instead."
}

Write-Host "Reinstalling greenlet and Playwright..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --upgrade --force-reinstall --no-cache-dir greenlet playwright

Write-Host "Applying the Playwright Firefox compatibility patch..." -ForegroundColor Cyan
& $VenvPython scripts\patch_playwright_driver.py

Write-Host "Validating native DLL loading..." -ForegroundColor Cyan
& $VenvPython -c "import struct, greenlet; from playwright.sync_api import sync_playwright; assert struct.calcsize('P') * 8 == 64, '64-bit Python is required'; print('greenlet and Playwright: OK')"

Write-Host "Repair complete. Run .\start-daemon.ps1." -ForegroundColor Green
