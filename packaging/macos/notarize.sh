#!/bin/bash
# Send a file to Apple's notary service and wait for the verdict.
#   APPLE_ID, APPLE_APP_PASSWORD (an app-specific password), APPLE_TEAM_ID in the environment.
# Usually minutes; a new developer account's first submissions can take hours, hence the long wait.
# Fails, printing Apple's log, unless the submission is Accepted. Never prints the password.
set -uo pipefail
file="$1"
: "${APPLE_ID:?}" "${APPLE_APP_PASSWORD:?}" "${APPLE_TEAM_ID:?}"
result="$(xcrun notarytool submit "$file" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
          --password "$APPLE_APP_PASSWORD" --wait --timeout 3h --output-format json)"
code=$?
field() { printf '%s' "$result" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('$1', ''))
except Exception: print('')"; }
status="$(field status)"; id="$(field id)"
echo "Notarization of $(basename "$file"): ${status:-no verdict} (submission ${id:-unknown}, notarytool exit $code)"
if [ "$status" = "Accepted" ]; then
  exit 0
fi
if [ -z "$status" ]; then
  echo "Apple has not finished yet. The submission keeps processing; Apple emails the account when it does."
  echo "$result"
  exit 1
fi
xcrun notarytool log "$id" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD" || true
exit 1
