[CmdletBinding()]
param(
    [string]$DaemonUrl = "http://127.0.0.1:8765"
)

$ErrorActionPreference = "Stop"

$Url = $DaemonUrl.TrimEnd("/") + "/cancel"
$result = Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json" -Body "{}"
$result | ConvertTo-Json -Depth 10
