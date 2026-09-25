#!/usr/bin/env bash
# Publish the Hermes dashboard (127.0.0.1:9119) at https://${DASHBOARD_FQDN} through Caddy.
# Refuses to open 80/443 unless Hermes reports auth_required=true. Safe to re-run.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly DASHBOARD_FQDN="${DASHBOARD_FQDN:?DASHBOARD_FQDN is required}"
readonly HERMES_STATUS_URL="${HERMES_STATUS_URL:-http://127.0.0.1:9119/api/status}"
readonly CADDYFILE_SRC="${REPO_DIR}/config/caddy/Caddyfile"
readonly CADDYFILE_DST=/etc/caddy/Caddyfile
readonly CADDY_DROPIN=/etc/systemd/system/caddy.service.d/dashboard.conf

log()  { printf '🔒 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

install_if_changed() {
  local src="$1" dst="$2"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    return 1
  fi
  install -m 0644 -D "${src}" "${dst}"
}

assert_hermes_requires_auth() {
  local status
  status="$(curl -fsS --max-time 5 "${HERMES_STATUS_URL}")" \
    || fail "Hermes dashboard not reachable at ${HERMES_STATUS_URL}; start it before publishing"
  grep -Eq '"auth_required"[[:space:]]*:[[:space:]]*true' <<<"${status}" \
    || fail "Hermes reports auth_required != true; configure OAuth/OIDC before publishing (see docs/runbooks/dashboard-domain.md)"
}

install_caddy_package() {
  dpkg-query -W -f='${Status}' caddy 2>/dev/null | grep -qx 'install ok installed' && return
  log "installing caddy from Ubuntu archive"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q caddy >/dev/null
}

install_service_dropin() {
  local dropin status=0
  dropin="$(mktemp)"
  printf '[Service]\nEnvironment=DASHBOARD_FQDN=%s\n' "${DASHBOARD_FQDN}" > "${dropin}"
  install_if_changed "${dropin}" "${CADDY_DROPIN}" || status=$?
  rm -f "${dropin}"
  return "${status}"
}

open_web_ports() {
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
}

verify_https() {
  curl -fsS -o /dev/null --retry 20 --retry-delay 3 --retry-all-errors \
    --resolve "${DASHBOARD_FQDN}:443:127.0.0.1" "https://${DASHBOARD_FQDN}/api/status" \
    || fail "https://${DASHBOARD_FQDN} not serving; check: journalctl -u caddy"
}

main() {
  [[ "${EUID}" -eq 0 ]] || fail "must run as root"
  log "publishing Hermes dashboard at https://${DASHBOARD_FQDN}"
  assert_hermes_requires_auth
  install_caddy_package

  local changed=0
  install_if_changed "${CADDYFILE_SRC}" "${CADDYFILE_DST}" && changed=1
  install_service_dropin && changed=1
  caddy validate --config "${CADDYFILE_DST}" --adapter caddyfile >/dev/null \
    || fail "Caddyfile is invalid"

  open_web_ports
  systemctl daemon-reload
  systemctl enable --now caddy.service >/dev/null
  if (( changed )); then
    systemctl restart caddy.service
  fi
  verify_https
  log "dashboard live at https://${DASHBOARD_FQDN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
