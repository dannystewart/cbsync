#!/bin/bash

# Start clipboard sync in the background
cd "$(dirname "$0")" || exit 1
nohup poetry run clipboard-sync > /tmp/clipboard-sync.log 2>&1 &

echo "Clipboard sync started!"
echo "Logs: tail -f /tmp/clipboard-sync.log"
echo "Stop: pkill -f clipboard-sync"
