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
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

function Register-LogonTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [string[]]$ExtraArgs
    )

    $quotedArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$ScriptPath`""
    ) + $ExtraArgs

    $action = New-ScheduledTaskAction `
        -Execute $PowerShell `
        -Argument ($quotedArgs -join " ") `
        -WorkingDirectory $ProjectDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Days 30) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
}

Register-LogonTask `
    -TaskName "OpenTable Automation Daemon" `
    -ScriptPath (Join-Path $ProjectDir "start-daemon.ps1") `
    -ExtraArgs @()

$pollerArgs = @(
    "-JobsUrl", "`"$JobsUrl`"",
    "-StatusMethod", $StatusMethod,
    "-DaemonUrl", "`"$DaemonUrl`"",
    "-Interval", "$Interval"
)
if ($StatusUrl) {
    $pollerArgs += @("-StatusUrl", "`"$StatusUrl`"")
}

Register-LogonTask `
    -TaskName "OpenTable Automation Poller" `
    -ScriptPath (Join-Path $ProjectDir "start-poller.ps1") `
    -ExtraArgs $pollerArgs

Write-Host "Registered startup tasks for $User." -ForegroundColor Green
Write-Host "They run when this Windows user logs in, which keeps Camoufox in an interactive desktop session."
