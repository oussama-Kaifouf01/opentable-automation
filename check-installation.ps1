[CmdletBinding()]
param(
    [string]$JobsUrl,
    [string]$StatusUrl,
    [string]$DaemonUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\setup-windows.ps1 first."
}

Set-Location -LiteralPath $ProjectDir
$healthArgs = @("run.py", "health-check", "--daemon-url", $DaemonUrl)
if ($JobsUrl) {
    $healthArgs += @("--jobs-url", $JobsUrl)
}
if ($StatusUrl) {
    $healthArgs += @("--status-url", $StatusUrl)
}
& $Python @healthArgs
