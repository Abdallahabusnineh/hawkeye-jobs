#!/bin/bash
# Send a file to Apple's notary service and wait for the verdict (a few minutes).
#   APPLE_ID, APPLE_APP_PASSWORD (an app-specific password), APPLE_TEAM_ID in the environment.
# Fails, printing Apple's log, unless the submission is Accepted. Never prints the password.
set -euo pipefail
file="$1"
: "${APPLE_ID:?}" "${APPLE_APP_PASSWORD:?}" "${APPLE_TEAM_ID:?}"
result="$(xcrun notarytool submit "$file" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
          --password "$APPLE_APP_PASSWORD" --wait --timeout 30m --output-format json)"
status="$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
id="$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')"
echo "Notarization of $(basename "$file"): $status ($id)"
if [ "$status" != "Accepted" ]; then
  xcrun notarytool log "$id" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" || true
  exit 1
fi
