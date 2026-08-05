[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\setup-windows.ps1 first."
}

Set-Location -LiteralPath $ProjectDir
& $Python scripts\patch_playwright_driver.py
if ($LASTEXITCODE -ne 0) {
    throw "The Playwright driver compatibility patch could not be applied."
}
& $Python run.py service --host $HostAddress --port $Port
