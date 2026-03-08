$pidFile = "$env:TEMP\cbsync.pid"
$stopped = $false

# Try PID file first for a clean targeted stop
if (Test-Path $pidFile) {
    $processId = Get-Content $pidFile
    try {
        Stop-Process -Id $processId -Force -ErrorAction Stop
        Write-Host "cbsync stopped (PID: $processId)"
        $stopped = $true
    }
    catch {
        Write-Host "PID $processId not found or already stopped"
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

# Fall back to killing by command line pattern to catch any instance regardless of how it was started
$matches = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'python3.exe'" |
    Where-Object { $_.CommandLine -like "*cbsync*" }

foreach ($proc in $matches) {
    try {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        Write-Host "cbsync stopped (PID: $($proc.ProcessId))"
        $stopped = $true
    }
    catch {
        # Already gone
    }
}

if (-not $stopped) {
    Write-Host "No running cbsync instance found."
}
