param(
    [switch]$NoPause
)

Set-Location $PSScriptRoot

# Ensure `import cbsync` works when running from repo root
$srcPath = Join-Path $PSScriptRoot "src"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $srcPath
}

# Start the process and capture its PID
try {
    $process = Start-Process -FilePath "python" -ArgumentList @("-m", "cbsync.main", "--supervise") -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\cbsync.log" -RedirectStandardError "$env:TEMP\cbsync-error.log" -PassThru
} catch {
    Write-Host "Failed to start cbsync: $($_.Exception.Message)"
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

# Save the PID to a file for easy stopping
$process.Id | Out-File -FilePath "$env:TEMP\cbsync.pid" -Encoding ASCII

Write-Host "cbsync started! (PID: $($process.Id))"
Write-Host "Logs: Get-Content $env:TEMP\cbsync.log -Wait"
Write-Host "Stop: Get-Content $env:TEMP\cbsync.pid | ForEach-Object { Stop-Process -Id $_ -Force }"

if (-not $NoPause) {
    Read-Host "Press Enter to close"
}
