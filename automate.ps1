[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir
& (Join-Path $ProjectDir "start-daemon.ps1")
