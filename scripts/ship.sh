#!/usr/bin/env bash
# Copy scripts/config/systemd to the server and run bootstrap.sh there.
# Secrets are read from `terraform output` and passed through the SSH environment,
# never written to disk locally and never echoed.
#
# Usage: scripts/ship.sh [user@]host      (default user: root for first run, ops afterwards)
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_DIR
readonly TARGET="${1:?usage: ship.sh [user@]host}"
readonly REMOTE_DIR="/tmp/hermes"

tf_output() { terraform -chdir="${REPO_DIR}/terraform" output -raw "$1"; }

read_restic_password() {
  if [[ -v RESTIC_PASSWORD ]]; then
    printf '%s' "${RESTIC_PASSWORD:?RESTIC_PASSWORD must not be empty}"
    return
  fi

  local file="${REPO_DIR}/.restic-password"
  if [[ ! -f "${file}" ]]; then
    umask 077
    openssl rand -base64 32 > "${file}"
    printf '🔑 generated new restic password in %s — store it in your password manager NOW\n' "${file}" >&2
  fi
  cat "${file}"
}

remote_sudo() {
  [[ "${TARGET}" == root@* ]] && printf '' || printf 'sudo '
}

main() {
  local admin_cidrs ops_key restic_repository restic_password access_key secret_key remote_environment remote_privilege
  admin_cidrs="$(tf_output admin_cidrs)"
  ops_key="$(tf_output ops_ssh_public_key)"
  restic_repository="$(tf_output restic_repository)"
  access_key="$(tf_output backup_access_key)"
  secret_key="$(tf_output backup_secret_key)"
  restic_password="$(read_restic_password)"

  printf '🚚 shipping repo to %s:%s\n' "${TARGET}" "${REMOTE_DIR}"
  remote_privilege="$(remote_sudo)"
  tar -C "${REPO_DIR}" -czf - scripts config systemd \
    | ssh "${TARGET}" "${remote_privilege}rm -rf ${REMOTE_DIR} && ${remote_privilege}mkdir -m 0700 ${REMOTE_DIR} && ${remote_privilege}tar -xzf - -C ${REMOTE_DIR}"

  printf '🔧 running bootstrap on %s\n' "${TARGET}"
  printf -v remote_environment 'ADMIN_CIDRS=%q\nOPS_SSH_PUBLIC_KEY=%q\nRESTIC_REPOSITORY=%q\nRESTIC_PASSWORD=%q\nAWS_ACCESS_KEY_ID=%q\nAWS_SECRET_ACCESS_KEY=%q\n' \
    "${admin_cidrs}" "${ops_key}" "${restic_repository}" "${restic_password}" "${access_key}" "${secret_key}"
  ssh "${TARGET}" "$(remote_sudo)bash -c 'set -a; source /dev/stdin; set +a; exec bash ${REMOTE_DIR}/scripts/bootstrap.sh </dev/null'" <<< "${remote_environment}"
}

main "$@"
