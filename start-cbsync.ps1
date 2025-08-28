Set-Location $PSScriptRoot

# Start the process and capture its PID
$process = Start-Process -FilePath "python" -ArgumentList "src\cbsync\cbsync.py" -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\cbsync.log" -RedirectStandardError "$env:TEMP\cbsync-error.log" -PassThru

# Save the PID to a file for easy stopping
$process.Id | Out-File -FilePath "$env:TEMP\cbsync.pid" -Encoding ASCII

Write-Host "cbsync started! (PID: $($process.Id))"
Write-Host "Logs: Get-Content $env:TEMP\cbsync.log -Wait"
Write-Host "Stop: Get-Content $env:TEMP\cbsync.pid | ForEach-Object { Stop-Process -Id $_ -Force }"
