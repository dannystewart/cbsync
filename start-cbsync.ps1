$pythonExe = "C:\Users\danny\AppData\Local\pypoetry\Cache\virtualenvs\cbsync-LIztAHNF-py3.14\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Python not found at: $pythonExe"
    Write-Host "Update the path in start-cbsync.ps1 to match the current Poetry virtualenv."
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

Set-Location $PSScriptRoot

try {
    $process = Start-Process -FilePath $pythonExe -ArgumentList @("-m", "cbsync.main", "--supervise") -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\cbsync.log" -RedirectStandardError "$env:TEMP\cbsync-error.log" -PassThru
} catch {
    Write-Host "Failed to start cbsync: $($_.Exception.Message)"
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

# Give it a moment and confirm it is still running
Start-Sleep -Milliseconds 1500
if ($process.HasExited) {
    Write-Host "cbsync exited immediately (code $($process.ExitCode)). Error log:"
    Get-Content "$env:TEMP\cbsync-error.log" -ErrorAction SilentlyContinue
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

$process.Id | Out-File -FilePath "$env:TEMP\cbsync.pid" -Encoding ASCII

Write-Host "cbsync started! (PID: $($process.Id))" -ForegroundColor Green
Write-Host "For logs, run: Get-Content $env:TEMP\cbsync.log -Wait"
Write-Host "To stop: Get-Content $env:TEMP\cbsync.pid | ForEach-Object { Stop-Process -Id $_ -Force }"
Write-Host ""
