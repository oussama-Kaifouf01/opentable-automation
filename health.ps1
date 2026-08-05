[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

& (Join-Path $ProjectDir "check-installation.ps1") `
    -JobsUrl "https://n8n.sheanswers.com/webhook/baritalia-get-queue" `
    -StatusUrl "https://n8n.sheanswers.com/webhook/baritalia-update-booking"

Write-Host ""
Write-Host "Local daemon:"
try {
    Invoke-RestMethod http://127.0.0.1:8765/health -TimeoutSec 5 | ConvertTo-Json -Depth 8
} catch {
    Write-Host "Not running or not reachable: $($_.Exception.Message)" -ForegroundColor Yellow
}
