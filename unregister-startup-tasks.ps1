[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

@(
    "OpenTable Automation Daemon",
    "OpenTable Automation Poller"
) | ForEach-Object {
    if (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $_ -Confirm:$false
        Write-Host "Removed task: $_"
    }
}
