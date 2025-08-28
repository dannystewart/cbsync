@echo off
cd /d "%~dp0"
start /B poetry run python src\clipboard_sync\clipboard_sync.py > %TEMP%\clipboard-sync.log 2>&1

echo Clipboard sync started!
echo Logs: type %TEMP%\clipboard-sync.log
echo Stop: taskkill /f /im python.exe /fi "WINDOWTITLE eq clipboard_sync*"
