[CmdletBinding()]
param(
    [string]$OutputDir = "delivery"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectName = Split-Path -Leaf $ProjectDir
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputRoot = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $ProjectDir $OutputDir }
$StagingRoot = Join-Path $OutputRoot ".staging"
$StagingDir = Join-Path $StagingRoot $ProjectName
$ZipPath = Join-Path $OutputRoot "opentable-automation-$Timestamp.zip"

function Assert-ChildPath {
    param(
        [string]$Parent,
        [string]$Child
    )
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childFull = [System.IO.Path]::GetFullPath($Child)
    if (-not $childFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate on path outside expected parent: $childFull"
    }
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
Assert-ChildPath -Parent $OutputRoot -Child $StagingRoot
if (Test-Path -LiteralPath $StagingRoot) {
    Remove-Item -LiteralPath $StagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

$ExcludedDirectories = @(
    ".venv",
    ".opentable-profile",
    ".opentable-profile-camoufox",
    ".opentable-profile-chromium",
    ".opentable-profile-test",
    "artifacts",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "delivery"
)
$ExcludedFiles = @(
    ".env",
    "config.json",
    "*.pyc",
    "*.pyo",
    "*.log"
)

Get-ChildItem -LiteralPath $ProjectDir -Force | ForEach-Object {
    if ($_.FullName.Equals($OutputRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }
    if ($_.PSIsContainer -and $ExcludedDirectories -contains $_.Name) {
        return
    }
    foreach ($pattern in $ExcludedFiles) {
        if ($_.Name -like $pattern) {
            return
        }
    }
    Copy-Item -LiteralPath $_.FullName -Destination $StagingDir -Recurse -Force
}

Get-ChildItem -LiteralPath $StagingDir -Recurse -Force -Directory |
    Where-Object { $ExcludedDirectories -contains $_.Name } |
    Sort-Object FullName -Descending |
    ForEach-Object {
        Assert-ChildPath -Parent $StagingRoot -Child $_.FullName
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }

foreach ($pattern in $ExcludedFiles) {
    Get-ChildItem -LiteralPath $StagingDir -Recurse -Force -File -Filter $pattern |
        ForEach-Object {
            Assert-ChildPath -Parent $StagingRoot -Child $_.FullName
            Remove-Item -LiteralPath $_.FullName -Force
        }
}

Compress-Archive -Path (Join-Path $StagingRoot "*") -DestinationPath $ZipPath -Force
Remove-Item -LiteralPath $StagingRoot -Recurse -Force

Write-Host "Created delivery package:" -ForegroundColor Green
Write-Host $ZipPath
Write-Host ""
Write-Host "Copy this zip to the restaurant mini PC, extract it, then run:"
Write-Host ".\install-target.ps1"
