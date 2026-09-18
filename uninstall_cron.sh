#!/bin/bash
PLIST_FILE="$HOME/Library/LaunchAgents/com.hawkeye-jobs.plist"
launchctl unload "$PLIST_FILE" 2>/dev/null || true
rm -f "$PLIST_FILE"
echo "OK: hawkeye-jobs stopped and removed"
