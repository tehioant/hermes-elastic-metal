#!/usr/bin/env bash
# Post a message to Discord via the webhook in /etc/hermes-host/alerts.env.
# No-op (logged) when alerts are not configured, so callers never fail because of it.
# Usage: notify.sh MESSAGE
set -Eeuo pipefail

readonly ALERTS_ENV=/etc/hermes-host/alerts.env
readonly MESSAGE="${1:?usage: notify.sh MESSAGE}"

if [[ ! -r "${ALERTS_ENV}" ]]; then
  logger -t notify "alerts not configured, dropped: ${MESSAGE}"
  exit 0
fi

# shellcheck disable=SC1090
. "${ALERTS_ENV}"

payload="$(jq -n --arg content "**$(hostname)** ${MESSAGE}" '{content: $content}')"
if ! curl -fsS --max-time 10 --retry 3 -H 'Content-Type: application/json' \
     -d "${payload}" "${DISCORD_WEBHOOK_URL:?missing in ${ALERTS_ENV}}" >/dev/null; then
  logger -p user.err -t notify "discord delivery failed: ${MESSAGE}"
  exit 1
fi
