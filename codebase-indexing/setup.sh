#!/bin/bash
# shellcheck disable=SC2155
set -e

# This file is kept here for backwards compatibility

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export RELATIVE_TS_FILE_PATH="setup.ts"

source "$SCRIPT_DIR/runtime.sh"