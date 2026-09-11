<#
.SYNOPSIS
Restarts the airgbmatrix state server on the main Windows PC.

.DESCRIPTION
Starts the scheduled task that keeps WSL alive, restarts the current or legacy
airgbmatrix systemd user service, and confirms that the server responds.

.EXAMPLE
Copy this file to the main PC, open PowerShell, and run:

    powershell -ExecutionPolicy Bypass -File .\Restart-AirgbMatrix.ps1

.EXAMPLE
Override the WSL distribution, scheduled task, or server URL:

    .\Restart-AirgbMatrix.ps1 `
        -Distro Ubuntu `
        -TaskName StartWSL `
        -ServerUrl http://localhost:5000/sessions
#>

param(
    [string]$Distro = "Ubuntu",
    [string]$TaskName = "StartWSL",
    [string]$ServerUrl = "http://localhost:5000/sessions"
)

$ErrorActionPreference = "Stop"

Write-Host "Starting WSL..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8

$serviceStarted = $false
foreach ($service in @("airgbmatrix.service", "claudergbmatrix.service")) {
    Write-Host "Trying $service..."
    wsl.exe -d $Distro -- systemctl --user restart $service 2>$null

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Restarted $service." -ForegroundColor Green
        $serviceStarted = $true
        break
    }
}

if (-not $serviceStarted) {
    throw "Neither airgbmatrix service could be restarted."
}

Start-Sleep -Seconds 2

try {
    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -TimeoutSec 5 `
        -Uri $ServerUrl
    Write-Host "airgbmatrix is online (HTTP $($response.StatusCode))." `
        -ForegroundColor Green
} catch {
    Write-Host "Service restarted, but $ServerUrl is unreachable." `
        -ForegroundColor Red
    wsl.exe -d $Distro -- systemctl --user --no-pager status `
        airgbmatrix.service claudergbmatrix.service
    throw
}
