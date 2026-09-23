#!/usr/bin/env bash
set -Eeuo pipefail

readonly ADMIN_CIDRS="${ADMIN_CIDRS:?ADMIN_CIDRS is required}"
readonly FIREWALL_STATE_FILE="${FIREWALL_STATE_FILE:-/etc/hermes/ssh-admin-cidrs}"

if ufw status | grep -q '^Status: active' && [[ ! -s "${FIREWALL_STATE_FILE}" ]]; then
  printf 'Active UFW has no recorded SSH allowlist; seed %s after auditing current rules\n' "${FIREWALL_STATE_FILE}" >&2
  exit 1
fi

ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for cidr in ${ADMIN_CIDRS//,/ }; do
  ufw allow from "${cidr}" to any port 22 proto tcp >/dev/null
done
ufw allow in on tailscale0 to any port 22 proto tcp >/dev/null
if [[ -s "${FIREWALL_STATE_FILE}" ]]; then
  previous_cidrs="$(<"${FIREWALL_STATE_FILE}")"
  for cidr in ${previous_cidrs//,/ }; do
    if [[ ",${ADMIN_CIDRS}," != *",${cidr},"* ]]; then
      ufw --force delete allow from "${cidr}" to any port 22 proto tcp >/dev/null
    fi
  done
fi
ufw --force enable >/dev/null
umask 077
mkdir -p "$(dirname "${FIREWALL_STATE_FILE}")"
printf '%s\n' "${ADMIN_CIDRS}" > "${FIREWALL_STATE_FILE}"
