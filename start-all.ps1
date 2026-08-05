[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$JobsUrl,
    [string]$StatusUrl,
    [ValidateSet("POST", "PUT")]
    [string]$StatusMethod = "PUT",
    [string]$DaemonUrl = "http://127.0.0.1:8765",
    [double]$Interval = 5
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Start-Process powershell.exe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ProjectDir "start-daemon.ps1")) `
    -WorkingDirectory $ProjectDir

Start-Sleep -Seconds 5

$pollerArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $ProjectDir "start-poller.ps1"),
        "-JobsUrl", $JobsUrl,
        "-StatusMethod", $StatusMethod,
        "-DaemonUrl", $DaemonUrl,
        "-Interval", "$Interval"
    )
if ($StatusUrl) {
    $pollerArgs += @("-StatusUrl", $StatusUrl)
}

Start-Process powershell.exe `
    -ArgumentList $pollerArgs `
    -WorkingDirectory $ProjectDir

Write-Host "Started daemon and poller windows."
