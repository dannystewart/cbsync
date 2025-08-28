#!/bin/bash

# Start clipboard sync in the background
cd "$(dirname "$0")" || exit 1
nohup poetry run python src/clipboard_sync/clipboard_sync.py > /tmp/clipboard-sync.log 2>&1 &

echo "Clipboard sync started!"
echo "Logs: tail -f /tmp/clipboard-sync.log"
echo "Stop: pkill -f clipboard_sync"
