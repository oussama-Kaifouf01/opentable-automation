[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$JobsUrl,
    [string]$StatusUrl,
    [string]$DaemonUrl = "http://127.0.0.1:8765",
    [ValidateSet("POST", "PUT")]
    [string]$StatusMethod = "PUT",
    [double]$Interval = 5
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\setup-windows.ps1 first."
}

Set-Location -LiteralPath $ProjectDir
$pollerArgs = @(
    "run.py", "poll-client",
    "--jobs-url", $JobsUrl,
    "--daemon-url", $DaemonUrl,
    "--status-method", $StatusMethod,
    "--interval", "$Interval"
)
if ($StatusUrl) {
    $pollerArgs += @("--status-url", $StatusUrl)
}
& $Python @pollerArgs
