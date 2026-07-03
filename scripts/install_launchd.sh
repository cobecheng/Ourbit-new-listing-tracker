#!/usr/bin/env bash
set -euo pipefail

LABEL="${LABEL:-com.cobecheng.ourbit-listing-tracker}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
TEMPLATE="$PROJECT_DIR/launchd/com.cobecheng.ourbit-listing-tracker.plist.template"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"

if [ ! -f "$PROJECT_DIR/.env" ]; then
  echo "Missing $PROJECT_DIR/.env. Copy .env.example to .env and fill Telegram settings first." >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Could not find an executable python3. Set PYTHON_BIN=/path/to/python3 and retry." >&2
  exit 1
fi

mkdir -p "$PLIST_DIR" "$PROJECT_DIR/logs" "$PROJECT_DIR/state"

escape_sed() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

label_escaped="$(escape_sed "$LABEL")"
project_escaped="$(escape_sed "$PROJECT_DIR")"
python_escaped="$(escape_sed "$PYTHON_BIN")"

sed \
  -e "s|__LABEL__|$label_escaped|g" \
  -e "s|__PROJECT_DIR__|$project_escaped|g" \
  -e "s|__PYTHON_BIN__|$python_escaped|g" \
  "$TEMPLATE" > "$PLIST_PATH"

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Installed and started $LABEL"
echo "Plist: $PLIST_PATH"
echo "Logs:"
echo "  $PROJECT_DIR/logs/ourbit-listing-tracker.out.log"
echo "  $PROJECT_DIR/logs/ourbit-listing-tracker.err.log"
