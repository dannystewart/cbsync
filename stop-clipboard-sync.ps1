$pidFile = "$env:TEMP\clipboard-sync.pid"

if (Test-Path $pidFile) {
    $processId = Get-Content $pidFile
    try {
        Stop-Process -Id $processId -Force
        Remove-Item $pidFile
        Write-Host "Clipboard sync stopped (PID: $processId)"
    }
    catch {
        Write-Host "Process $processId not found or already stopped"
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host "No clipboard sync PID file found. Process may not be running."
}
