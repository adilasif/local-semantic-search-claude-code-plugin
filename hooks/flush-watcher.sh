#!/bin/bash
# Stop hook: Signal watcher to flush pending changes immediately
#
# Sends SIGUSR1 to the watcher process to immediately process any
# pending file changes before the Claude session ends.

set -e

# Find PID file for current directory
PID_DIR="/tmp/semantic-watcher"
PWD_HASH=$(echo -n "$PWD" | sha256sum | cut -c1-16)
PID_FILE="$PID_DIR/$PWD_HASH.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        # Send SIGUSR1 to trigger immediate flush
        kill -USR1 "$PID" 2>/dev/null || true

        # Give it a moment to process
        sleep 0.5
    fi
fi
