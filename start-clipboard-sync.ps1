Set-Location $PSScriptRoot
Start-Process -FilePath "python" -ArgumentList "src\clipboard_sync\clipboard_sync.py" -WindowStyle Hidden -RedirectStandardOutput "$env:TEMP\clipboard-sync.log" -RedirectStandardError "$env:TEMP\clipboard-sync.log"

Write-Host "Clipboard sync started!"
Write-Host "Logs: Get-Content $env:TEMP\clipboard-sync.log -Wait"
Write-Host "Stop: Get-Process python | Where-Object {$_.ProcessName -eq 'python'} | Stop-Process"
