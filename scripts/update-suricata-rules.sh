#!/usr/bin/env bash
# Root-only free ET Open updates: validate candidate, atomically promote, then reload.
set -Eeuo pipefail
[[ "${EUID}" == 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }
ETC=/etc/hermes-suricata
STATE=/var/lib/hermes-suricata
RULES="${STATE}/rules"
LIB=/usr/local/lib/hermes-suricata
[[ "$(<"${ETC}/managed-by-hermes")" == hermes-suricata-managed-v1 ]]
/usr/bin/python3 - "${ETC}" "${RULES}" "${STATE}/update" "${LIB}" <<'PY'
import os, pathlib, sys
for value in sys.argv[1:]:
    p = pathlib.Path(value)
    for q in [p, *p.parents]:
        if q.is_symlink():
            sys.exit('symlink refused: ' + str(q))
    st = p.stat()
    if st.st_uid != 0 or st.st_mode & 0o022:
        sys.exit('root-protected directory required: ' + str(p))
    for root, dirs, files in os.walk(p):
        for name in dirs + files:
            if pathlib.Path(root, name).is_symlink():
                sys.exit('symlink descendant refused')
PY
exec 9>"${ETC}/rules-update.lock"
flock -n 9 || exit 0
stage="$(mktemp -d "${RULES}/.candidate.XXXXXX")"
trap 'rm -rf "${stage}"' EXIT
# A dedicated config prevents adoption of /etc/suricata/update.yaml.
# Never enable paid feeds or additional sources.
# suricata-update also loads data-dir/sources; forbid any additional feed.
/usr/bin/python3 - "${STATE}/update/sources" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
if p.exists() and any(p.iterdir()):
    sys.exit('Additional rule sources are forbidden; only free ET Open is approved')
PY
printf '%s\n' 'sources:' '  - https://rules.emergingthreats.net/open/suricata-%(__version__)s/emerging.rules.tar.gz' > "${stage}/update.yaml"
# Distro rules can contain IPS actions even when ET Open itself is alert-only.
# Exclude known non-alert actions, never rewrite them into alerts. The independent
# action gate below still rejects unexpected actions or updater regressions.
printf '%s\n' 're: ^(?:drop|pass|reject(?:src|dst|both)?)\s+' > "${stage}/disable.conf"
/usr/bin/suricata-update --config "${stage}/update.yaml" \
  --disable-conf "${stage}/disable.conf" \
  --suricata /usr/bin/suricata --suricata-conf "${ETC}/suricata.yaml" \
  --data-dir "${STATE}/update" --output "${stage}" --no-reload --no-test --fail
/usr/bin/python3 "${LIB}/config.py" check-rules "${stage}/suricata.rules" "${ETC}/local.rules"
# -S overrides rule-files; combine both sets so local rules are also validated.
cat "${stage}/suricata.rules" "${ETC}/local.rules" > "${stage}/validation.rules"
/usr/bin/suricata -T --init-errors-fatal -c "${ETC}/suricata.yaml" -S "${stage}/validation.rules" -l "${stage}"
chown root:hermes-suricata "${stage}/suricata.rules"
chmod 0640 "${stage}/suricata.rules"
if [[ -f "${RULES}/suricata.rules" ]]; then
  cp -p "${RULES}/suricata.rules" "${stage}/previous.rules"
fi
mv -f "${stage}/suricata.rules" "${RULES}/suricata.rules"
if systemctl is-active --quiet hermes-suricata.service; then
  # Suricata 8's Rust client takes the socket positionally, not legacy -s.
  # Blocking reload plus JSON return check avoids mistaking queued work for success.
  if ! /usr/bin/suricatasc -c ruleset-reload-rules /run/hermes-suricata/suricata-command.socket \
      | /usr/bin/python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin)['return'] == 'OK' else 1)"; then
    if [[ -f "${stage}/previous.rules" ]]; then
      mv -f "${stage}/previous.rules" "${RULES}/suricata.rules"
    fi
    printf 'Reload request failed; previous rules restored.\n' >&2
    exit 1
  fi
fi
