$pidFile = "$env:TEMP\cbsync.pid"
$heartbeatFiles = Get-ChildItem -Path $env:TEMP -Filter "cbsync-heartbeat-*.json" -ErrorAction SilentlyContinue

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

    foreach ($heartbeatFile in $heartbeatFiles) {
        try {
            $hb = Get-Content $heartbeatFile.FullName | ConvertFrom-Json
            if ($hb.pid) {
                try {
                    Stop-Process -Id $hb.pid -Force
                    Write-Host "Worker stopped (PID: $($hb.pid))"
                } catch {
                    # ignore
                }
            }
        } catch {
            # ignore
        }
        Remove-Item $heartbeatFile.FullName -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host "No cbsync PID file found. Process may not be running."
}
