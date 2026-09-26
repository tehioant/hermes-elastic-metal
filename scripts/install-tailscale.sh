#!/usr/bin/env bash
# Install Tailscale from its signed apt repo and join the tailnet via an interactive login URL.
# Independent from bootstrap.sh: a failure here never blocks host setup. Safe to re-run.
# Usage (needs a TTY for the login URL): ssh -t ops@HOST 'sudo bash /tmp/hermes/scripts/install-tailscale.sh'
set -Eeuo pipefail

readonly KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
readonly SOURCES_LIST=/etc/apt/sources.list.d/tailscale.list
readonly NODE_HOSTNAME="${TAILSCALE_HOSTNAME:-emeta-01}"
readonly NODE_TAG=tag:metal

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

is_tagged() {
  tailscale status --json | jq -e --arg tag "${NODE_TAG}" '.Self.Tags // [] | index($tag)' >/dev/null
}

join_tailnet() {
  if is_logged_in && is_tagged; then
    log "already connected to the tailnet as ${NODE_TAG}"
    return
  fi
  log "joining as ${NODE_TAG}; if a login URL appears, open it to approve ${NODE_HOSTNAME}"
  tailscale up --hostname="${NODE_HOSTNAME}" --advertise-tags="${NODE_TAG}" --ssh=false \
    || fail "tailscale up failed (is ${NODE_TAG} defined in tailscale/policy.hujson and applied?)"
}

verify() {
  is_logged_in || fail "node is not connected"
  is_tagged || fail "node is not tagged ${NODE_TAG}"
  log "✅ connected as ${NODE_HOSTNAME} (${NODE_TAG}) — $(tailscale ip -4)"
}

main() {
  require_root
  install_package
  join_tailnet
  verify
}

main "$@"
