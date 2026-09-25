#!/usr/bin/env bash
# Install oauth2-proxy (pinned release, SHA-256 verified) on 127.0.0.1:4180 as the Google
# login in front of Netdata. Reuses the Hermes Google OAuth client. Safe to re-run.
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly NETDATA_FQDN="${NETDATA_FQDN:?NETDATA_FQDN is required}"
readonly HERMES_ENV="${HERMES_ENV:-/home/ops/.hermes/.env}"
readonly OAUTH2_PROXY_VERSION=v7.15.4
readonly OAUTH2_PROXY_SHA256=4fbe902189aab713d9c0519b90a645032d4636ecb523dc36f5cc312d8ebef1e2
readonly OAUTH2_PROXY_RELEASE="oauth2-proxy-${OAUTH2_PROXY_VERSION}.linux-amd64"
readonly OAUTH2_PROXY_URL="https://github.com/oauth2-proxy/oauth2-proxy/releases/download/${OAUTH2_PROXY_VERSION}/${OAUTH2_PROXY_RELEASE}.tar.gz"
readonly OAUTH2_PROXY_BIN=/usr/local/bin/oauth2-proxy
readonly CONFIG_SRC="${REPO_DIR}/config/oauth2-proxy/netdata.cfg"
readonly CONFIG_DST=/etc/oauth2-proxy/netdata.cfg
readonly ENV_FILE=/etc/oauth2-proxy/netdata.env
readonly UNIT=oauth2-proxy-netdata.service
readonly UNIT_SRC="${REPO_DIR}/systemd/${UNIT}"
readonly UNIT_DST="/etc/systemd/system/${UNIT}"

WORKDIR="$(mktemp -d)"
readonly WORKDIR
trap 'rm -rf "${WORKDIR}"' EXIT

log()  { printf '🔑 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

install_if_changed() {
  local src="$1" dst="$2" mode="$3"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    return 1
  fi
  install -m "${mode}" -D "${src}" "${dst}"
}

enable_and_refresh_service() {
  local unit="$1" config_changed="$2"
  systemctl daemon-reload
  systemctl enable --now "${unit}" >/dev/null
  if (( config_changed )); then
    systemctl restart "${unit}"
  fi
}

installed_version() {
  [[ -x "${OAUTH2_PROXY_BIN}" ]] || return 0
  "${OAUTH2_PROXY_BIN}" --version 2>&1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1
}

install_oauth2_proxy_binary() {
  [[ "$(installed_version)" == "${OAUTH2_PROXY_VERSION}" ]] && return 1
  log "installing oauth2-proxy ${OAUTH2_PROXY_VERSION}"
  curl -fsSL "${OAUTH2_PROXY_URL}" -o "${WORKDIR}/release.tar.gz" \
    || fail "oauth2-proxy download failed: ${OAUTH2_PROXY_URL}"
  printf '%s  %s\n' "${OAUTH2_PROXY_SHA256}" "${WORKDIR}/release.tar.gz" | sha256sum --check --quiet - \
    || fail "oauth2-proxy checksum mismatch; refusing to install"
  tar -xzf "${WORKDIR}/release.tar.gz" -C "${WORKDIR}"
  install -m 0755 "${WORKDIR}/${OAUTH2_PROXY_RELEASE}/oauth2-proxy" "${OAUTH2_PROXY_BIN}"
}

env_value() {
  local file="$1" key="$2" value
  value="$(grep -E "^${key}=" "${file}" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  value=${value#[\"\']}
  value=${value%[\"\']}
  printf '%s' "${value}"
}

required_hermes_value() {
  local key="$1" value
  value="$(env_value "${HERMES_ENV}" "${key}")"
  [[ -n "${value}" ]] \
    || fail "${key} missing in ${HERMES_ENV}; configure Hermes Google OIDC first (docs/runbooks/dashboard-domain.md)"
  printf '%s' "${value}"
}

cookie_secret() {
  local existing
  existing="$(env_value "${ENV_FILE}" OAUTH2_PROXY_COOKIE_SECRET)"
  if [[ -n "${existing}" ]]; then
    printf '%s' "${existing}"
  else
    openssl rand -base64 32 | tr -- '+/' '-_'
  fi
}

write_env_file() {
  local client_id client_secret secret rendered="${WORKDIR}/netdata.env"
  client_id="$(required_hermes_value HERMES_DASHBOARD_OIDC_CLIENT_ID)"
  client_secret="$(required_hermes_value HERMES_DASHBOARD_OIDC_CLIENT_SECRET)"
  secret="$(cookie_secret)"
  (
    umask 077
    cat > "${rendered}" <<EOF
OAUTH2_PROXY_CLIENT_ID=${client_id}
OAUTH2_PROXY_CLIENT_SECRET=${client_secret}
OAUTH2_PROXY_COOKIE_SECRET=${secret}
OAUTH2_PROXY_REDIRECT_URL=https://${NETDATA_FQDN}/oauth2/callback
OAUTH2_PROXY_WHITELIST_DOMAINS=${NETDATA_FQDN}
EOF
  )
  install_if_changed "${rendered}" "${ENV_FILE}" 0600
}

verify_ping() {
  curl -fs -o /dev/null --retry 10 --retry-delay 1 --retry-connrefused http://127.0.0.1:4180/ping \
    || fail "oauth2-proxy not answering on 127.0.0.1:4180; check: journalctl -u ${UNIT}"
}

main() {
  [[ "${EUID}" -eq 0 ]] || fail "must run as root"
  log "configuring oauth2-proxy (Google login) for https://${NETDATA_FQDN}"

  local changed=0
  install_oauth2_proxy_binary && changed=1
  write_env_file && changed=1
  install_if_changed "${CONFIG_SRC}" "${CONFIG_DST}" 0644 && changed=1
  install_if_changed "${UNIT_SRC}" "${UNIT_DST}" 0644 && changed=1

  enable_and_refresh_service "${UNIT}" "${changed}"
  verify_ping
  log "oauth2-proxy ready on 127.0.0.1:4180"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
