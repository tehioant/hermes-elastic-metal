#!/usr/bin/env bash
set -Eeuo pipefail

admin_key_file="${1:?explicit ops public key path required}"
deploy_key_file="${2:?deploy public key path required}"
ops_keys="${3:?ops authorized_keys path required}"

validate_key_file() {
  local path="$1" pattern="$2"
  local -a lines
  mapfile -t lines < "${path}"
  if [[ ${#lines[@]} -ne 1 || ! "${lines[0]}" =~ ${pattern} ]] || ! ssh-keygen -lf "${path}" >/dev/null 2>&1; then
    printf 'Expected exactly one valid public key in %s\n' "${path}" >&2
    exit 1
  fi
}

umask 077
admin_tmp="$(mktemp "${ops_keys}.admin.XXXXXX")"
keys_tmp="$(mktemp "${ops_keys}.XXXXXX")"
trap 'rm -f "${admin_tmp}" "${keys_tmp}"' EXIT

if [[ -v OPS_SSH_PUBLIC_KEY ]]; then
  printf '%s\n' "${OPS_SSH_PUBLIC_KEY}" > "${admin_tmp}"
  validate_key_file "${admin_tmp}" '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+)[[:space:]]+[A-Za-z0-9+/=]+([[:space:]].*)?$'
  admin_source="${admin_tmp}"
else
  admin_source="${admin_key_file}"
  [[ -s "${admin_source}" ]] || { printf 'Missing explicit ops public key at %s\n' "${admin_key_file}" >&2; exit 1; }
  validate_key_file "${admin_source}" '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+)[[:space:]]+[A-Za-z0-9+/=]+([[:space:]].*)?$'
fi

if [[ -f "${deploy_key_file}" ]]; then
  validate_key_file "${deploy_key_file}" '^restrict[[:space:]]+ssh-ed25519[[:space:]]+[A-Za-z0-9+/=]+([[:space:]].*)?$'
fi

install -d -m 0700 "$(dirname "${admin_key_file}")"
if [[ -v OPS_SSH_PUBLIC_KEY ]]; then
  install -m 0600 "${admin_tmp}" "${admin_key_file}"
fi
printf '%s\n' "$(<"${admin_source}")" > "${keys_tmp}"
if [[ -f "${deploy_key_file}" ]]; then
  printf '%s\n' "$(<"${deploy_key_file}")" >> "${keys_tmp}"
fi
mv "${keys_tmp}" "${ops_keys}"
