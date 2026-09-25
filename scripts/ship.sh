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
  local admin_cidrs restic_repository restic_password access_key secret_key
  admin_cidrs="$(tf_output admin_cidrs)"
  restic_repository="$(tf_output restic_repository)"
  access_key="$(tf_output backup_access_key)"
  secret_key="$(tf_output backup_secret_key)"
  restic_password="$(read_restic_password)"

  printf '🚚 shipping repo to %s:%s\n' "${TARGET}" "${REMOTE_DIR}"
  tar -C "${REPO_DIR}" -czf - scripts config systemd \
    | ssh "${TARGET}" "rm -rf ${REMOTE_DIR} && mkdir -p ${REMOTE_DIR} && tar -xzf - -C ${REMOTE_DIR}"

  printf '🔧 running bootstrap on %s\n' "${TARGET}"
  # Heredoc expands client-side on purpose: it carries the secrets to the remote env.
  # shellcheck disable=SC2087
  ssh "${TARGET}" "$(remote_sudo)bash -c 'set -a; source /dev/stdin; set +a; exec bash ${REMOTE_DIR}/scripts/bootstrap.sh </dev/null'" <<EOF
ADMIN_CIDRS='${admin_cidrs}'
RESTIC_REPOSITORY='${restic_repository}'
RESTIC_PASSWORD='${restic_password}'
AWS_ACCESS_KEY_ID='${access_key}'
AWS_SECRET_ACCESS_KEY='${secret_key}'
EOF

  install_netdata
  publish_dashboard
}

install_netdata() {
  local operator_target="${TARGET/#root@/ops@}"
  printf '📈 installing Netdata on %s\n' "${operator_target}"
  ssh "${operator_target}" "sudo bash ${REMOTE_DIR}/scripts/install-netdata.sh </dev/null" \
    || printf '⚠️  Netdata install failed; host bootstrap is complete. Re-run: ssh %s sudo bash %s/scripts/install-netdata.sh\n' "${operator_target}" "${REMOTE_DIR}" >&2
}

publish_dashboard() {
  local operator_target="${TARGET/#root@/ops@}" fqdn
  fqdn="$(tf_output dashboard_fqdn)"
  printf '🔒 publishing dashboard at https://%s\n' "${fqdn}"
  ssh "${operator_target}" "sudo DASHBOARD_FQDN=${fqdn} bash ${REMOTE_DIR}/scripts/install-caddy.sh </dev/null" \
    || printf '⚠️  Dashboard not published; host bootstrap is complete. See docs/runbooks/dashboard-domain.md\n' >&2
}

main "$@"
