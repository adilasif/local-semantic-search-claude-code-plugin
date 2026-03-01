#!/bin/bash
# shellcheck disable=SC2155
set -e

runtime_exists() {
  which "$1" >/dev/null 2>&1
}

configure_runtime() {
  local runtime="$1"
  case "$runtime" in
    bun)
      TS_RUNTIME="bun run"
      ;;
    deno)
      TS_RUNTIME="deno run --allow-all"
      ;;
    node)
      TS_RUNTIME="npx --yes tsx"
      ;;
  esac
}

try_runtime() {
  local runtime="$1"
  
  case "$runtime" in
    bun)
      runtime_exists bun || return 1
      ;;
    deno)
      runtime_exists deno || return 1
      ;;
    node)
      if ! runtime_exists node; then
        return 1
      elif ! runtime_exists npx; then
        echo "  node found but npx not available"
        echo "     Note: npx comes with Node.js v5.2+"
        return 1
      fi
      ;;
    *)
      return 1
      ;;
  esac
  
  configure_runtime "$runtime"
  return 0
}

get_ts_runtime() {
  if [ -n "$RUNTIME" ]; then
    case "$RUNTIME" in
      bun|deno|node)
        if try_runtime "$RUNTIME"; then
          return 0
        else
          echo "  RUNTIME=$RUNTIME specified but not available"
          return 1
        fi
        ;;
      *)
        echo "  Invalid RUNTIME='$RUNTIME'"
        echo "     Valid: bun, deno, node"
        return 1
        ;;
    esac
  fi
  
  for runtime in bun deno node; do
    if try_runtime "$runtime"; then
      return 0
    fi
  done
  
  echo "No TypeScript runtime found"
  echo ""
  echo "Install Bun (recommended):"
  echo "  curl -fsSL https://bun.com/install | bash"
  return 1
}


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! get_ts_runtime; then
  exit 1
fi

if [ ! -f "$SCRIPT_DIR/$RELATIVE_TS_FILE_PATH" ]; then
  echo "Error: $RELATIVE_TS_FILE_PATH not found in $SCRIPT_DIR/src"
  exit 1
fi

RUN_COMMAND="$TS_RUNTIME \"$SCRIPT_DIR/$RELATIVE_TS_FILE_PATH\""
echo "Executing: $RUN_COMMAND"
$TS_RUNTIME "$SCRIPT_DIR/$RELATIVE_TS_FILE_PATH"