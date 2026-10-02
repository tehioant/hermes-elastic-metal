#!/usr/bin/env python3
"""Run real Loki + Alloy + Grafana against isolated, test-only log fixtures."""
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOKI_VERSION = "3.7.8"
LOKI_SHA256 = "62aea42c9cba52cd1642b3666ab37019a0ce4c24ab50b07e85dccc8d812f7d61"
LOKI_URL = f"https://github.com/grafana/loki/releases/download/v{LOKI_VERSION}/loki-linux-amd64.zip"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url, *, data=None, headers=None, auth=None):
    headers = dict(headers or {})
    if auth:
        import base64
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, response.read()


def wait_for(url, process, log_path, auth=None):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited {process.returncode}: {log_path.read_text(errors='replace')[-5000:]}")
        try:
            return request(url, auth=auth)
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise TimeoutError(f"service did not become ready: {log_path.read_text(errors='replace')[-5000:]}")


def loki_binary(work):
    configured = os.environ.get("LOKI_BIN")
    if configured:
        return Path(configured)
    archive = work / "loki.zip"
    urllib.request.urlretrieve(LOKI_URL, archive)
    import hashlib
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != LOKI_SHA256:
        raise RuntimeError(f"Loki archive SHA-256 mismatch: {actual}")
    with zipfile.ZipFile(archive) as bundle:
        target = work / "loki"
        target.write_bytes(bundle.read("loki-linux-amd64"))
    target.chmod(0o755)
    return target


def replace_source(config, source, replacement):
    pattern = rf'loki\.source\.journal "{source}" \{{.*?^\}}'
    result, count = re.subn(pattern, replacement, config, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise RuntimeError(f"failed to adapt production Alloy {source} source for fixture input")
    return result


def main():
    alloy = os.environ.get("ALLOY_BIN") or shutil.which("alloy")
    grafana = os.environ.get("GRAFANA_SERVER") or shutil.which("grafana-server")
    grafana_home = Path(os.environ.get("GRAFANA_HOME", "/usr/share/grafana"))
    if not alloy or not grafana or not grafana_home.is_dir():
        raise SystemExit("smoke prerequisites missing: install Alloy and Grafana native binaries (see CI workflow)")

    scratch_root = Path(os.environ.get("TMPDIR", "/home/ops/.hermes/cache/scratch"))
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="security-dashboard-smoke-", dir=scratch_root) as temporary:
        work = Path(temporary)
        loki_port, alloy_port, grafana_port = free_port(), free_port(), free_port()
        loki_cfg = (ROOT / "config/security-dashboard/loki.yaml").read_text()
        loki_cfg = loki_cfg.replace("127.0.0.1", "127.0.0.1", 1).replace("3100", str(loki_port)).replace("9096", str(free_port()))
        loki_cfg = loki_cfg.replace("/var/lib/hermes-security-dashboard/loki", str(work / "loki-data"))
        loki_file = work / "loki.yaml"
        loki_file.write_text(loki_cfg)

        (work / "ufw.log").write_text("[UFW BLOCK] IN=eth0 OUT= MAC=aa SRC=198.51.100.7 DST=192.0.2.2 PROTO=TCP DPT=22\n")
        (work / "ssh.log").write_text("sshd[123]: Failed password for invalid user test from 203.0.113.9 port 22 ssh2\n")
        (work / "fail2ban.log").write_text("2026-01-01 12:00:00,000 fail2ban.actions [12]: NOTICE [sshd] Ban 203.0.113.9\n")
        caddy = {"level": "info", "ts": 1780000000, "logger": "http.log.access", "msg": "handled request",
                 "request": {"remote_ip": "192.0.2.8", "remote_port": "1234", "client_ip": "192.0.2.8",
                             "proto": "HTTP/2.0", "method": "GET", "host": "example.test",
                             "uri": "/admin?token=QUERY_SECRET", "headers": {"Authorization": ["HEADER_SECRET"]}},
                 "status": 401, "size": 10, "duration": 0.1, "user_id": "test-user"}
        caddy_named = caddy | {"logger": "http.log.access.log0", "status": 403}
        caddy_other = caddy | {"logger": "tls.issuance", "status": 599}
        (work / "caddy.log").write_text(
            "\n".join(json.dumps(entry) for entry in (caddy, caddy_named, caddy_other))
            + "\nnot-json HEADER_SECRET\n")

        alloy_cfg = (ROOT / "config/security-dashboard/config.alloy").read_text()
        journal_remote = os.environ.get("JOURNAL_REMOTE_BIN", "/usr/lib/systemd/systemd-journal-remote")
        if not Path(journal_remote).is_file():
            raise SystemExit("install systemd-journal-remote or set JOURNAL_REMOTE_BIN for the isolated journal fixture")
        journal_dir = work / "journal"
        journal_dir.mkdir()
        timestamp = time.time_ns() // 1000
        entries = []
        sources = [
            ({"_TRANSPORT": "kernel", "SYSLOG_IDENTIFIER": "kernel"}, (work / "ufw.log").read_text()),
            ({"_COMM": "sshd-session"}, (work / "ssh.log").read_text()),
            ({"_COMM": "sshd"}, "Accepted publickey for test-user from 203.0.113.9 port 12345 ssh2\n"),
            ({"_SYSTEMD_UNIT": "caddy.service"}, (work / "caddy.log").read_text()),
            ({"_SYSTEMD_UNIT": "unrelated.service"}, "UNRELATED_SECRET\n"),
            ({"_TRANSPORT": "kernel", "SYSLOG_IDENTIFIER": "kernel"}, "non-firewall KERNEL_SECRET\n"),
        ]
        for fields, messages in sources:
            for message in messages.splitlines():
                values = fields | {
                    "__REALTIME_TIMESTAMP": str(timestamp), "__MONOTONIC_TIMESTAMP": str(len(entries) + 1),
                    "_BOOT_ID": "00000000000000000000000000000001",
                    "_MACHINE_ID": "00000000000000000000000000000002", "MESSAGE": message,
                }
                entries.append("\n".join(f"{key}={value}" for key, value in values.items()))
                timestamp += 1
        export = work / "journal.export"
        export.write_text("\n\n".join(entries) + "\n\n")
        subprocess.run([journal_remote, "--split-mode=none", f"--output={journal_dir / 'fixtures.journal'}", str(export)],
                       check=True, cwd=work)
        for name in ("ufw", "ssh", "ssh_session", "caddy"):
            pattern = rf'loki\.source\.journal "{name}" \{{.*?^\}}'
            original = re.search(pattern, alloy_cfg, flags=re.MULTILINE | re.DOTALL)
            assert original, name
            replacement = original.group().replace("{", f'{{\n  path = "{journal_dir}"', 1)
            alloy_cfg = replace_source(alloy_cfg, name, replacement)
        alloy_cfg = alloy_cfg.replace("/var/log/fail2ban.log", str(work / "fail2ban.log"))
        alloy_cfg = alloy_cfg.replace("constants.hostname", '"test-only"')
        alloy_cfg = alloy_cfg.replace("http://127.0.0.1:3100", f"http://127.0.0.1:{loki_port}")
        alloy_file = work / "config.alloy"
        alloy_file.write_text(alloy_cfg)
        subprocess.run([alloy, "validate", str(alloy_file)], check=True, cwd=work)

        provisioning = work / "provisioning"
        (provisioning / "datasources").mkdir(parents=True)
        (provisioning / "dashboards").mkdir(parents=True)
        (work / "dashboards").mkdir()
        (provisioning / "datasources/loki.yaml").write_text(
            (ROOT / "config/security-dashboard/provisioning/datasources/loki.yaml").read_text().replace(
                "http://127.0.0.1:3100", f"http://127.0.0.1:{loki_port}"))
        provider = (ROOT / "config/security-dashboard/provisioning/dashboards/security.yaml").read_text()
        (provisioning / "dashboards/security.yaml").write_text(provider.replace(
            "/etc/hermes-security-dashboard/dashboards", str(work / "dashboards")))
        (work / "dashboards/security.json").write_text((ROOT / "config/security-dashboard/dashboard.json").read_text())
        grafana_cfg = (ROOT / "config/security-dashboard/grafana.ini").read_text()
        grafana_cfg = grafana_cfg.replace("http_port = 3001", f"http_port = {grafana_port}")
        grafana_cfg = grafana_cfg.replace("/var/lib/hermes-security-dashboard/grafana", str(work / "grafana-data"))
        grafana_cfg = grafana_cfg.replace("/var/log/hermes-security-dashboard/grafana", str(work / "grafana-logs"))
        grafana_cfg = grafana_cfg.replace("/etc/hermes-security-dashboard/provisioning", str(provisioning))
        grafana_file = work / "grafana.ini"
        grafana_file.write_text(grafana_cfg)
        import secrets
        admin_password = secrets.token_hex(32)
        env = os.environ | {"GF_SECURITY_ADMIN_PASSWORD": admin_password,
                            "GF_SECURITY_SECRET_KEY": "smoke-test-only-secret",
                            "GF_PATHS_DATA": str(work / "grafana-data"),
                            "GF_PATHS_LOGS": str(work / "grafana-logs"),
                            "GF_PATHS_PROVISIONING": str(provisioning)}
        for path in (work / "grafana-data", work / "grafana-logs", work / "loki-data"):
            path.mkdir()

        processes = []
        logs = []
        try:
            loki_log = work / "loki.out"
            logs.append(loki_log)
            processes.append(subprocess.Popen([str(loki_binary(work)), f"-config.file={loki_file}"], cwd=work,
                                              stdout=loki_log.open("w"), stderr=subprocess.STDOUT, env=env))
            wait_for(f"http://127.0.0.1:{loki_port}/ready", processes[-1], loki_log)
            alloy_log = work / "alloy.out"
            logs.append(alloy_log)
            processes.append(subprocess.Popen([alloy, "run", "--disable-reporting", "--storage.path", str(work / "alloy-data"),
                                               "--server.http.listen-addr", f"127.0.0.1:{alloy_port}", str(alloy_file)],
                                              cwd=work, stdout=alloy_log.open("w"), stderr=subprocess.STDOUT, env=env))
            wait_for(f"http://127.0.0.1:{alloy_port}/-/ready", processes[-1], alloy_log)
            grafana_log = work / "grafana.out"
            logs.append(grafana_log)
            processes.append(subprocess.Popen([grafana, f"--config={grafana_file}", f"--homepath={grafana_home}", "--packaging=deb"],
                                              cwd=work, stdout=grafana_log.open("w"), stderr=subprocess.STDOUT, env=env))
            grafana_url = f"http://127.0.0.1:{grafana_port}"
            wait_for(grafana_url + "/api/health", processes[-1], grafana_log, f"admin:{admin_password}")

            expected = {"ufw": "UFW BLOCK", "ssh": "Failed password", "fail2ban": "Ban 203.0.113.9",
                        "caddy": "status=401 remote_ip=192.0.2.8 method=GET host=example.test path=/admin user_id=test-user"}
            deadline = time.monotonic() + 60
            results = {}
            while time.monotonic() < deadline:
                results = {}
                for job in expected:
                    query = urllib.parse.urlencode({"query": f'{{job="{job}"}}', "limit": "20", "direction": "backward",
                                                    "start": str(int((time.time() - 300) * 1e9)), "end": str(int(time.time() * 1e9))})
                    try:
                        _, body = request(f"http://127.0.0.1:{loki_port}/loki/api/v1/query_range?{query}")
                    except Exception:
                        print("\n".join(f"--- {path.name} ---\n{path.read_text(errors='replace')[-6000:]}" for path in logs))
                        raise
                    results[job] = json.loads(body)["data"]["result"]
                if all(results[job] for job in expected):
                    ssh_lines = "\n".join(value for stream in results["ssh"] for _, value in stream["values"])
                    caddy_lines = "\n".join(value for stream in results["caddy"] for _, value in stream["values"])
                    if "Failed password" in ssh_lines and "Accepted publickey" in ssh_lines and "status=403" in caddy_lines:
                        break
                time.sleep(1)
            else:
                details = "\n".join(f"--- {p.name} ---\n{p.read_text(errors='replace')[-5000:]}" for p in logs)
                raise AssertionError(f"Alloy fixture entries did not reach Loki: {results}; {details}")
            for job, needle in expected.items():
                lines = "\n".join(value for stream in results[job] for _, value in stream["values"])
                assert needle in lines, f"{job} result missing {needle!r}: {lines}"
            caddy_lines = "\n".join(value for stream in results["caddy"] for _, value in stream["values"])
            assert "status=403" in caddy_lines, "named Caddy access logger was excluded"
            assert "status=599" not in caddy_lines, "non-access Caddy logs were ingested"
            assert "QUERY_SECRET" not in caddy_lines and "HEADER_SECRET" not in caddy_lines and "token=" not in caddy_lines
            for streams in results.values():
                for stream in streams:
                    labels = stream["stream"]
                    assert labels["job"] in expected and labels["host"] == "test-only", labels
                    assert not {"filename", "remote_ip", "client_ip", "path", "uri"} & set(labels), labels
                    assert set(labels) <= {"job", "host", "detected_level", "service_name"}, labels

            query = urllib.parse.urlencode({"query": '{host="test-only"}', "limit": "100"})
            _, body = request(f"http://127.0.0.1:{loki_port}/loki/api/v1/query_range?{query}")
            all_streams = json.loads(body)["data"]["result"]
            all_lines = "\n".join(value for stream in all_streams for _, value in stream["values"])
            assert "UNRELATED_SECRET" not in all_lines and "KERNEL_SECRET" not in all_lines, all_lines
            assert {stream["stream"]["job"] for stream in all_streams} == set(expected)
            _, dashboard_body = request(grafana_url + "/api/dashboards/uid/host-security", auth=f"admin:{admin_password}")
            dashboard = json.loads(dashboard_body)["dashboard"]
            assert dashboard["uid"] == "host-security"
            # Exercise every panel via Grafana's actual Loki datasource, not just its presence.
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    query = target | {
                        "datasource": panel["datasource"],
                        "expr": target["expr"].replace("$__interval", "1m").replace("$__range", "5m"),
                        "intervalMs": 60000,
                        "maxDataPoints": 100,
                    }
                    payload = json.dumps({"queries": [query], "from": "now-5m", "to": "now"}).encode()
                    _, response = request(grafana_url + "/api/ds/query", data=payload,
                                          headers={"Content-Type": "application/json"}, auth=f"admin:{admin_password}")
                    result = json.loads(response)["results"][target["refId"]]
                    assert not result.get("error"), (panel["title"], result)
                    assert result.get("frames"), (panel["title"], result)
            _, health_body = request(grafana_url + "/api/datasources/uid/local-loki/health", auth=f"admin:{admin_password}")
            assert json.loads(health_body)["status"] == "OK", health_body
            print("Security dashboard smoke PASS: real Loki, Alloy ingestion/privacy, Grafana datasource/dashboard provisioning")
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
