Set-Location $PSScriptRoot

# Start the process and capture its PID
$process = Start-Process -FilePath "python" -ArgumentList "src\clipboard_sync\clipboard_sync.py" -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\clipboard-sync.log" -RedirectStandardError "$env:TEMP\clipboard-sync-error.log" -PassThru

# Save the PID to a file for easy stopping
$process.Id | Out-File -FilePath "$env:TEMP\clipboard-sync.pid" -Encoding ASCII

Write-Host "Clipboard sync started! (PID: $($process.Id))"
Write-Host "Logs: Get-Content $env:TEMP\clipboard-sync.log -Wait"
Write-Host "Stop: Get-Content $env:TEMP\clipboard-sync.pid | ForEach-Object { Stop-Process -Id $_ -Force }"
