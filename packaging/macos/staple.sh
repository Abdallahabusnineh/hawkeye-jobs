#!/bin/bash
# Attach Apple's notarization ticket to an app or disk image. Stapling downloads the ticket from
# Apple's CloudKit service, which sometimes drops the connection (stapler error 68 / -1005):
# retry a few times before failing the release.
set -uo pipefail
target="$1"
for attempt in 1 2 3 4 5; do
  if xcrun stapler staple "$target"; then
    exit 0
  fi
  echo "Stapling failed (attempt $attempt of 5); retrying in $((attempt * 15)) s"
  sleep $((attempt * 15))
done
exit 1
