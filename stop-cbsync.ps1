$pidFile = "$env:TEMP\cbsync.pid"

if (Test-Path $pidFile) {
    $processId = Get-Content $pidFile
    try {
        Stop-Process -Id $processId -Force
        Remove-Item $pidFile
        Write-Host "cbsync stopped (PID: $processId)"
    }
    catch {
        Write-Host "Process $processId not found or already stopped"
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host "No cbsync PID file found. Process may not be running."
}
