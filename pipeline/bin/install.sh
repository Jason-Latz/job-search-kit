#!/bin/bash
# Install the daily LaunchAgent. Re-run after moving the repo.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.jobsearchkit.digest"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
sed "s|__REPO__|$REPO|g" "$REPO/bin/$LABEL.plist" > "$DEST"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$DEST"
echo "installed $DEST"
launchctl print "gui/$UID/$LABEL" | grep -E "state|program|runs" | head -5 || true
echo
echo "run now:      launchctl kickstart -k gui/$UID/$LABEL"
echo "uninstall:    launchctl bootout gui/$UID/$LABEL && rm $DEST"
