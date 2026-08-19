param([string]$BaseUrl = "http://127.0.0.1:8765")
$ErrorActionPreference = "Stop"
$result = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -Method Get -TimeoutSec 5
if ($result.status -ne "ready") { throw "QQ Group Summary is not ready" }
Write-Host "QQ Group Summary is ready"
