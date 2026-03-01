#!/bin/bash
# SessionStart hook: Start file watcher if this project is indexed
#
# Checks if the current directory has an indexed collection in Qdrant.
# If so, starts the debounced file watcher in the background.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$SCRIPT_DIR")}"

# Get collection for current directory
COLLECTION=$("$SCRIPT_DIR/get-collection.py" "$PWD" 2>/dev/null)

if [ -n "$COLLECTION" ]; then
    # Create PID file directory
    PID_DIR="/tmp/semantic-watcher"
    mkdir -p "$PID_DIR"

    # Use hash of PWD for unique PID file name
    PWD_HASH=$(echo -n "$PWD" | sha256sum | cut -c1-16)
    PID_FILE="$PID_DIR/$PWD_HASH.pid"

    # Check if watcher is already running for this directory
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
            # Watcher already running
            exit 0
        fi
    fi

    # Start watcher in background using plugin's venv
    VENV_PYTHON="$PLUGIN_ROOT/.venv/bin/python"
    if [ -x "$VENV_PYTHON" ]; then
        "$VENV_PYTHON" "$SCRIPT_DIR/run-watcher.py" "$PWD" "$COLLECTION" &
    else
        # Fallback to system python (may fail if deps not installed)
        python3 "$SCRIPT_DIR/run-watcher.py" "$PWD" "$COLLECTION" &
    fi
    WATCHER_PID=$!

    # Save PID for later cleanup/signaling
    echo "$WATCHER_PID" > "$PID_FILE"
fi
