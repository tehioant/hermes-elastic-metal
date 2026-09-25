#!/usr/bin/env bash
# Run the Hermes dashboard on 127.0.0.1:9119 (hermes-dashboard.service) and publish it
# at https://${DASHBOARD_FQDN} through Caddy. Refuses to open 80/443 unless Hermes
# reports auth_required=true with the self-hosted (Google) OIDC provider. Safe to re-run.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly DASHBOARD_FQDN="${DASHBOARD_FQDN:?DASHBOARD_FQDN is required}"
readonly HERMES_STATUS_URL="${HERMES_STATUS_URL:-http://127.0.0.1:9119/api/status}"
readonly CADDYFILE_SRC="${REPO_DIR}/config/caddy/Caddyfile"
readonly CADDYFILE_DST=/etc/caddy/Caddyfile
readonly CADDY_DROPIN=/etc/systemd/system/caddy.service.d/dashboard.conf
readonly DASHBOARD_UNIT=hermes-dashboard.service
readonly DASHBOARD_UNIT_SRC="${REPO_DIR}/systemd/${DASHBOARD_UNIT}"
readonly DASHBOARD_UNIT_DST="/etc/systemd/system/${DASHBOARD_UNIT}"
readonly AUTH_REQUIRED_PATTERN='"auth_required"[[:space:]]*:[[:space:]]*true'
readonly OIDC_PROVIDER_PATTERN='"auth_providers"[[:space:]]*:[[:space:]]*\[[^]]*"self-hosted"'

log()  { printf '🔒 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

install_if_changed() {
  local src="$1" dst="$2"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    return 1
  fi
  install -m 0644 -D "${src}" "${dst}"
}

enable_and_refresh_service() {
  local unit="$1" config_changed="$2"
  systemctl daemon-reload
  systemctl enable --now "${unit}" >/dev/null
  if (( config_changed )); then
    systemctl restart "${unit}"
  fi
}

start_hermes_dashboard() {
  log "starting ${DASHBOARD_UNIT} on 127.0.0.1:9119"
  local changed=0
  install_if_changed "${DASHBOARD_UNIT_SRC}" "${DASHBOARD_UNIT_DST}" && changed=1
  enable_and_refresh_service "${DASHBOARD_UNIT}" "${changed}"
}

assert_hermes_requires_oidc() {
  local status
  status="$(curl -fsS --max-time 5 --retry 30 --retry-delay 2 --retry-all-errors "${HERMES_STATUS_URL}")" \
    || fail "Hermes dashboard not reachable at ${HERMES_STATUS_URL}; check: journalctl -u ${DASHBOARD_UNIT} (missing OIDC config makes it refuse to start)"
  grep -Eq "${AUTH_REQUIRED_PATTERN}" <<<"${status}" \
    || fail "Hermes reports auth_required != true; configure Google OIDC before publishing (see docs/runbooks/dashboard-domain.md)"
  grep -Eq "${OIDC_PROVIDER_PATTERN}" <<<"${status}" \
    || fail "Hermes auth provider is not self-hosted OIDC (password-only is unsafe in public); configure Google OIDC (see docs/runbooks/dashboard-domain.md)"
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
  start_hermes_dashboard
  assert_hermes_requires_oidc
  install_caddy_package

  local changed=0
  install_if_changed "${CADDYFILE_SRC}" "${CADDYFILE_DST}" && changed=1
  install_service_dropin && changed=1
  caddy validate --config "${CADDYFILE_DST}" --adapter caddyfile >/dev/null \
    || fail "Caddyfile is invalid"

  open_web_ports
  enable_and_refresh_service caddy.service "${changed}"
  verify_https
  log "dashboard live at https://${DASHBOARD_FQDN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
