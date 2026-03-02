#!/bin/bash

# Start cbsync in the background
cd "$(dirname "$0")" || exit 1
nohup poetry run cbsync --supervise > /tmp/cbsync.log 2>&1 &

echo "cbsync started!"
echo "Logs: tail -f /tmp/cbsync.log"
echo "Stop: pkill -f cbsync"
