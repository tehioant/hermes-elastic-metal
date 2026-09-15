#!/usr/bin/env bash
# Restrict inbound traffic to published container ports.
# Docker bypasses ufw; DOCKER-USER is the only chain Docker leaves to us.
set -Eeuo pipefail

readonly DOCKER_NETWORKS="172.16.0.0/12"

iptables -F DOCKER-USER 2>/dev/null || true
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A DOCKER-USER -i lo -j RETURN
iptables -A DOCKER-USER -s "${DOCKER_NETWORKS}" -j RETURN

if [[ -r /etc/docker/published-ports.allow ]]; then
  while read -r port source; do
    [[ -z "${port}" || "${port}" == \#* ]] && continue
    iptables -A DOCKER-USER -p tcp --dport "${port}" -s "${source:-0.0.0.0/0}" -j RETURN
  done < /etc/docker/published-ports.allow
fi

iptables -A DOCKER-USER -j DROP
