#!/usr/bin/env bash
# Bootstrap emeta-01 as a hardened Docker host. Safe to re-run.
#
# Required env:  ADMIN_CIDRS   comma-separated CIDRs allowed to SSH
# Optional env:  DOCKER_USER   unprivileged operator account (default: ops)
#                RESTIC_REPOSITORY, RESTIC_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
#                (all four required together to configure backups)
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly DOCKER_USER="${DOCKER_USER:-ops}"
readonly ADMIN_CIDRS="${ADMIN_CIDRS:?ADMIN_CIDRS is required, e.g. 203.0.113.4/32}"
readonly EXPECTED_UBUNTU_VERSION="26.04"

log()  { printf '🔧 %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*" >&2; }
fail() { printf '❌ %s\n' "$*" >&2; exit 1; }
trap 'fail "bootstrap failed at line ${LINENO}"' ERR

install_if_changed() {
  local src="$1" dst="$2" mode="${3:-0644}"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    return 1
  fi
  install -m "${mode}" -D "${src}" "${dst}"
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || fail "must run as root"
}

assert_supported_os() {
  . /etc/os-release
  [[ "${ID}" == "ubuntu" && "${VERSION_ID}" == "${EXPECTED_UBUNTU_VERSION}" ]] \
    || fail "expected Ubuntu ${EXPECTED_UBUNTU_VERSION}, found ${PRETTY_NAME}"
}

warn_if_no_avx2() {
  if ! grep -qw avx2 /proc/cpuinfo; then
    warn "CPU has no AVX2: images built for x86-64-v3 will crash with SIGILL"
  fi
}

install_base_packages() {
  log "installing base packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    ca-certificates curl gnupg ufw fail2ban unattended-upgrades \
    smartmontools mdadm restic chrony needrestart jq rasdaemon
}

install_docker() {
  if dpkg -s docker-ce >/dev/null 2>&1; then
    log "docker engine already installed"
    return
  fi
  log "installing docker engine from download.docker.com"
  . /etc/os-release
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "${VERSION_CODENAME}" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

configure_docker_daemon() {
  log "configuring docker daemon"
  systemctl enable --now docker
  if install_if_changed "${REPO_DIR}/config/docker/daemon.json" /etc/docker/daemon.json; then
    systemctl restart docker
  fi
}

create_docker_user() {
  log "ensuring operator user ${DOCKER_USER}"
  id -u "${DOCKER_USER}" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "${DOCKER_USER}"
  usermod -aG docker,sudo "${DOCKER_USER}"
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "${DOCKER_USER}" > "/etc/sudoers.d/${DOCKER_USER}"
  chmod 0440 "/etc/sudoers.d/${DOCKER_USER}"
  visudo -cf "/etc/sudoers.d/${DOCKER_USER}" >/dev/null

  local ssh_dir="/home/${DOCKER_USER}/.ssh"
  install -d -m 0700 -o "${DOCKER_USER}" -g "${DOCKER_USER}" "${ssh_dir}"
  # Cloud images set a forced-command restriction on root's authorized_keys
  # entries (blocks direct root login); strip any leading key-options so it
  # is not carried over to the ops user.
  grep -oE '(ssh-[a-z0-9]+|ecdsa-sha2-[a-z0-9-]+) .*' /root/.ssh/authorized_keys \
    > "${ssh_dir}/authorized_keys"
  chmod 0600 "${ssh_dir}/authorized_keys"
  chown "${DOCKER_USER}:${DOCKER_USER}" "${ssh_dir}/authorized_keys"
}

harden_ssh() {
  log "hardening sshd (root login disabled, keys only)"
  if install_if_changed "${REPO_DIR}/config/ssh/99-hardening.conf" /etc/ssh/sshd_config.d/99-hardening.conf; then
    sshd -t
    systemctl restart ssh
  fi
}

configure_firewall() {
  log "configuring ufw (ssh from ${ADMIN_CIDRS}) and DOCKER-USER chain"
  ufw --force reset >/dev/null
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  for cidr in ${ADMIN_CIDRS//,/ }; do
    ufw allow from "${cidr}" to any port 22 proto tcp >/dev/null
  done
  ufw --force enable >/dev/null

  install_if_changed "${REPO_DIR}/config/docker/docker-user-rules.sh" /etc/docker/docker-user-rules.sh 0755 || true
  touch /etc/docker/published-ports.allow
  install_if_changed "${REPO_DIR}/systemd/docker-user-rules.service" /etc/systemd/system/docker-user-rules.service || true
  systemctl daemon-reload
  systemctl enable docker-user-rules.service >/dev/null
  systemctl restart docker-user-rules.service
}

configure_auto_updates() {
  log "enabling unattended security upgrades"
  install_if_changed "${REPO_DIR}/config/apt/52unattended-upgrades-local" /etc/apt/apt.conf.d/52unattended-upgrades-local || true
  systemctl enable --now unattended-upgrades >/dev/null
  systemctl enable --now fail2ban >/dev/null
}

configure_disk_monitoring() {
  log "enabling smartd, mdmonitor and rasdaemon"
  sed -i 's|^DEVICESCAN.*|DEVICESCAN -a -o on -S on -s (S/../.././02) -m root|' /etc/smartd.conf
  systemctl enable --now smartmontools >/dev/null
  systemctl enable --now mdmonitor >/dev/null 2>&1 || warn "mdmonitor unavailable (no md arrays?)"
  systemctl enable --now rasdaemon >/dev/null
}

package_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -qx 'install ok installed'
}

assert_managed_netdata() {
  local marker="$1"
  if package_installed netdata; then
    [[ -f "${marker}" ]] \
      || fail "existing Netdata was not installed by this bootstrap; inspect it before replacing it"
  fi
}

netdata_listens_beyond_loopback() {
  ss -H -ltn '( sport = :19999 )' | awk '$4 != "127.0.0.1:19999" { found = 1 } END { exit !found }'
}

install_netdata() {
  log "configuring Netdata (localhost only)"
  local config_changed=0 installer
  local config_src="${REPO_DIR}/config/netdata/netdata.conf" config_dst=/etc/netdata/netdata.conf
  local managed_marker=/etc/netdata/.hermes-native-install
  assert_managed_netdata "${managed_marker}"
  # Place the bind restriction before installation starts the service.
  if install_if_changed "${config_src}" "${config_dst}"; then
    config_changed=1
  fi
  install -m 0644 /dev/null /etc/netdata/.opt-out-from-anonymous-statistics

  if ! package_installed netdata; then
    installer="$(mktemp)"
    curl -fsSL https://get.netdata.cloud/kickstart.sh -o "${installer}"
    DISABLE_TELEMETRY=1 bash "${installer}" --non-interactive --release-channel stable --native-only --auto-update
    rm -f "${installer}"
    install -m 0644 /dev/null "${managed_marker}"
    config_changed=1
  fi
  package_installed netdata-repo || fail "Netdata's official package repository is not installed"

  # Restore the repo-owned configuration if a package upgrade changed it.
  if install_if_changed "${config_src}" "${config_dst}"; then
    config_changed=1
  fi
  systemctl enable --now netdata.service >/dev/null
  if (( config_changed )); then
    systemctl restart netdata.service
  fi
  curl -fsS --retry 5 --retry-delay 1 http://127.0.0.1:19999/api/v1/info >/dev/null
  if netdata_listens_beyond_loopback; then
    fail "Netdata is listening beyond 127.0.0.1:19999"
  fi
}

backup_env_provided() {
  [[ -n "${RESTIC_REPOSITORY:-}" && -n "${RESTIC_PASSWORD:-}" \
     && -n "${AWS_ACCESS_KEY_ID:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]]
}

install_backup() {
  install -m 0755 "${REPO_DIR}/scripts/backup.sh" /usr/local/sbin/backup.sh
  install -m 0755 "${REPO_DIR}/scripts/restore-drill.sh" /usr/local/sbin/restore-drill.sh

  if ! backup_env_provided; then
    warn "restic env not provided: backups NOT configured (re-run with RESTIC_* and AWS_* set)"
    return
  fi

  log "configuring restic repository"
  install -d -m 0700 /etc/restic
  printf '%s\n' "${RESTIC_PASSWORD}" > /etc/restic/password
  cat > /etc/restic/env <<EOF
RESTIC_REPOSITORY=${RESTIC_REPOSITORY}
RESTIC_PASSWORD_FILE=/etc/restic/password
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
EOF
  chmod 0600 /etc/restic/env /etc/restic/password

  # shellcheck disable=SC1091
  ( set -a; . /etc/restic/env; set +a; restic cat config >/dev/null 2>&1 || restic init )

  install_if_changed "${REPO_DIR}/systemd/restic-backup.service" /etc/systemd/system/restic-backup.service || true
  install_if_changed "${REPO_DIR}/systemd/restic-backup.timer" /etc/systemd/system/restic-backup.timer || true
  systemctl daemon-reload
  systemctl enable --now restic-backup.timer >/dev/null
}

install_maintenance_timers() {
  log "installing prune and healthcheck timers"
  install -m 0755 "${REPO_DIR}/scripts/healthcheck.sh" /usr/local/sbin/healthcheck.sh
  local unit
  for unit in docker-prune.service docker-prune.timer healthcheck.service healthcheck.timer; do
    install_if_changed "${REPO_DIR}/systemd/${unit}" "/etc/systemd/system/${unit}" || true
  done
  systemctl daemon-reload
  systemctl enable --now docker-prune.timer healthcheck.timer >/dev/null
}

verify() {
  log "verifying"
  docker run --rm busybox:1.37 sh -c 'echo "✅ busybox OK: $(uname -srm)"'
  docker info --format '✅ docker {{.ServerVersion}} | storage={{.Driver}} | cgroup={{.CgroupVersion}}'
  printf '✅ raid: %s\n' "$(grep -E '^md' /proc/mdstat | tr '\n' ' ')"
  systemctl list-timers --no-pager --all 'docker-prune.timer' 'healthcheck.timer' 'restic-backup.timer'
}

main() {
  require_root
  assert_supported_os
  warn_if_no_avx2
  install_base_packages
  install_docker
  configure_docker_daemon
  create_docker_user
  harden_ssh
  configure_firewall
  configure_auto_updates
  configure_disk_monitoring
  install_netdata
  install_backup
  install_maintenance_timers
  verify
  log "bootstrap completed — reconnect as ${DOCKER_USER}@, root login is now disabled"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
