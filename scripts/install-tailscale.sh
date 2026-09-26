#!/usr/bin/env bash
# Install Tailscale from its signed apt repo and join the tailnet via an interactive login URL.
# Independent from bootstrap.sh: a failure here never blocks host setup. Safe to re-run.
# Usage (needs a TTY for the login URL): ssh -t ops@HOST 'sudo bash /tmp/hermes/scripts/install-tailscale.sh'
set -Eeuo pipefail

readonly KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
readonly SOURCES_LIST=/etc/apt/sources.list.d/tailscale.list
readonly NODE_HOSTNAME="${TAILSCALE_HOSTNAME:-emeta-01}"

log()  { printf '🔒 %s\n' "$*"; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ ${EUID} -eq 0 ]] || fail "run as root (sudo)"
}

ubuntu_codename() {
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID}" == ubuntu && -n "${VERSION_CODENAME:-}" ]] || fail "unsupported OS: ${ID:-unknown}"
  printf '%s' "${VERSION_CODENAME}"
}

add_apt_repository() {
  local codename base_url
  codename="$(ubuntu_codename)"
  base_url="https://pkgs.tailscale.com/stable/ubuntu/${codename}"
  log "adding Tailscale apt repo for ${codename}"
  curl -fsSL "${base_url}.noarmor.gpg" -o "${KEYRING}" || fail "cannot download Tailscale signing key"
  curl -fsSL "${base_url}.tailscale-keyring.list" -o "${SOURCES_LIST}" || fail "cannot download Tailscale apt source"
}

install_package() {
  if command -v tailscale >/dev/null; then
    log "tailscale already installed: $(tailscale version | head -1)"
    return
  fi
  add_apt_repository
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q tailscale
  systemctl enable --now tailscaled >/dev/null
}

is_logged_in() {
  tailscale status --json 2>/dev/null | grep -q '"BackendState": *"Running"'
}

join_tailnet() {
  if is_logged_in; then
    log "already connected to the tailnet"
    return
  fi
  log "open the login URL below to approve ${NODE_HOSTNAME}"
  tailscale up --hostname="${NODE_HOSTNAME}" --ssh=false || fail "tailscale up failed"
}

verify() {
  is_logged_in || fail "node is not connected"
  log "✅ connected as ${NODE_HOSTNAME} — $(tailscale ip -4)"
}

main() {
  require_root
  install_package
  join_tailnet
  verify
}

main "$@"
