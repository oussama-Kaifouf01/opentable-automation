[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

& (Join-Path $ProjectDir "start-poller.ps1") `
    -JobsUrl "https://n8n.sheanswers.com/webhook/baritalia-get-queue" `
    -StatusUrl "https://n8n.sheanswers.com/webhook/baritalia-update-booking" `
    -StatusMethod PUT
