#!/usr/bin/python3
"""Delete only recognized daily Wazuh alert indices and expired compressed manager logs."""
import argparse
import base64
from datetime import date, timedelta
import json
from pathlib import Path
import re
import ssl
import subprocess
import sys
import urllib.request

CONFIG = Path("/etc/hermes-wazuh")


def expired(indices, today):
    cutoff = today - timedelta(days=30)
    selected = []
    for entry in indices:
        name = entry["index"]
        match = re.fullmatch(r"wazuh-alerts-4\.x-(\d{4})\.(\d{2})\.(\d{2})", name)
        if match:
            try:
                timestamp = date(*(int(part) for part in match.groups()))
            except ValueError:
                continue
            if timestamp < cutoff:
                selected.append(name)
    return sorted(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, help="offline index fixture; no network/deletions")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(expired(json.loads(args.plan.read_text()), args.today)))
        return
    if (CONFIG / ".managed").read_text() != "hermes-wazuh-v1\n":
        raise RuntimeError("not a managed deployment")
    credential = json.loads((CONFIG / "credentials.json").read_text())["indexer"]
    auth = "Basic " + base64.b64encode(("admin:" + credential).encode()).decode()
    context = ssl.create_default_context(cafile=str(CONFIG / "certs/root-ca.pem"))

    def request(path, method="GET"):
        req = urllib.request.Request("https://127.0.0.1:9200/" + path, method=method, headers={"Authorization": auth})
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            return json.load(response)

    selected = expired(request("_cat/indices?format=json&h=index"), args.today)
    for name in selected:
        request(name, "DELETE")
        # Read back exact target: deletion acknowledgements are not sufficient.
        if any(item["index"] == name for item in request("_cat/indices?format=json&h=index")):
            raise RuntimeError("deleted index still present")
    # Manager-only named volume, no host log mounts. Never delete current alerts.json/ossec.log.
    code = r'''import pathlib,re,time
root=pathlib.Path('/var/ossec/logs')
cutoff=time.time()-30*86400
removed=0
for p in root.rglob('*.gz'):
    if p.is_symlink():
        continue
    if re.fullmatch(r'ossec-(alerts|archive)-\d{2}\.(json|log)\.gz',p.name) and p.stat().st_mtime < cutoff:
        p.unlink()
        removed+=1
print(removed)
'''
    result = subprocess.run(["docker", "compose", "-p", "hermes-wazuh", "-f", str(CONFIG / "compose.yml"),
                             "exec", "-T", "wazuh.manager", "/var/ossec/framework/python/bin/python3", "-c", code],
                            capture_output=True)
    if result.returncode:
        raise RuntimeError("manager log retention failed; inspect manager locally")
    print(f"Retention: deleted {len(selected)} old alert indices; manager compressed-log cleanup completed")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f"Wazuh retention: {exc}", file=sys.stderr)
        sys.exit(1)
