#!/usr/bin/env bash
# Install (or remove) a daily background sync via launchd on macOS.
#
#   tools/install-daily-sync.sh            # run every day at 07:30
#   tools/install-daily-sync.sh 18 00      # ...or at 18:00
#   tools/install-daily-sync.sh --uninstall
#
# The job runs `sync.py --quiet`, so it writes to the log only when something
# actually changed. Logs: cache/sync.log (and cache/sync.err for failures).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.canvas-obsidian.dailysync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed daily sync"
  exit 0
fi

HOUR="${1:-7}"; MINUTE="${2:-30}"
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || { echo "no venv at $PY — create it first (see README)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/cache"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$REPO/sync.py</string>
    <string>--quiet</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key><string>$REPO/cache/sync.log</string>
  <key>StandardErrorPath</key><string>$REPO/cache/sync.err</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

printf 'installed: daily sync at %02d:%02d\n' "$HOUR" "$MINUTE"
echo "  logs:      $REPO/cache/sync.log   (written only when something changed)"
echo "  run now:   launchctl kickstart gui/$UID/$LABEL"
echo "  remove:    tools/install-daily-sync.sh --uninstall"
