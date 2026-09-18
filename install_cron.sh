#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$PROJECT_DIR/hawkeye.log"
PYTHON="$PROJECT_DIR/venv/bin/python3"
ENV_FILE="$PROJECT_DIR/.env"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.hawkeye-jobs.plist"

echo "hawkeye-jobs — installer"
echo ""

if [ ! -x "$PYTHON" ]; then
  echo "ERROR: virtualenv not found at $PYTHON"
  echo "Run this first:"
  echo "  python3 -m venv venv"
  echo "  venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [ -f "$ENV_FILE" ]; then
  echo "Found existing .env — keeping it."
else
  read -r -p "Gmail address: " GMAIL_USER
  read -r -s -p "Gmail App Password (16 chars): " GMAIL_PASS
  echo ""
  read -r -p "Recipient email [${GMAIL_USER}]: " RECIPIENT_EMAIL
  RECIPIENT_EMAIL="${RECIPIENT_EMAIL:-$GMAIL_USER}"

  cat > "$ENV_FILE" << EOF
GMAIL_USER=${GMAIL_USER}
GMAIL_PASS=${GMAIL_PASS}
RECIPIENT_EMAIL=${RECIPIENT_EMAIL}
EOF
  chmod 600 "$ENV_FILE"
  echo "Wrote $ENV_FILE"
fi

chmod +x "$PROJECT_DIR/run.sh"

mkdir -p "$PLIST_DIR"
cat > "$PLIST_FILE" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.hawkeye-jobs</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$PROJECT_DIR/run.sh</string></array>
    <key>StartInterval</key><integer>7200</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$LOG_FILE</string>
    <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load "$PLIST_FILE"

echo "Installed LaunchAgent: $PLIST_FILE"
echo "Runs every 2 hours from: $PROJECT_DIR"
echo "Running a test now..."
bash "$PROJECT_DIR/run.sh"
echo "Done. Check hawkeye.log and your inbox."
