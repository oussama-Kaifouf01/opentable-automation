[CmdletBinding()]
param(
    [switch]$NoStartup
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

$ReservationsUrl = "https://guestcenter.opentable.com/restaurant/161727/front-of-house#/reservations/live"
$JobsUrl = "https://n8n.sheanswers.com/webhook/baritalia-get-queue"
$StatusUrl = "https://n8n.sheanswers.com/webhook/baritalia-update-booking"
$StatusMethod = "PUT"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

Write-Step "Installing dependencies"
& (Join-Path $ProjectDir "setup-windows.ps1")

Write-Step "Configuring Bar Italia GuestCenter URL"
$ConfigPath = Join-Path $ProjectDir "config.json"
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$config.browser.timeout_ms = 60000
$config.admin.reservations_url = $ReservationsUrl
$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding ASCII

Write-Step "Creating .env if missing"
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir ".env"))) {
    Copy-Item -LiteralPath (Join-Path $ProjectDir ".env.example") -Destination (Join-Path $ProjectDir ".env")
}

if (-not $NoStartup) {
    Write-Step "Registering Windows startup tasks"
    & (Join-Path $ProjectDir "register-startup-tasks.ps1") `
        -JobsUrl $JobsUrl `
        -StatusUrl $StatusUrl `
        -StatusMethod $StatusMethod
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next:"
Write-Host "1. Run: notepad .env"
Write-Host "2. Add OpenTable email/password and save."
Write-Host "3. Run: .\login.ps1"
Write-Host "4. Run: .\start.ps1"
