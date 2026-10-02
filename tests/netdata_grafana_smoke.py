#!/usr/bin/env python3
"""Exercise the provisioned Prometheus bridge against a real local Netdata endpoint."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def dashboard_expressions():
    dashboard = json.loads((ROOT / "config/netdata-grafana/dashboard.json").read_text())
    return [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]


def main():
    prometheus = os.environ.get("PROMETHEUS_BIN") or shutil.which("prometheus")
    if not prometheus:
        raise SystemExit("smoke prerequisite missing: set PROMETHEUS_BIN or install the Prometheus binary")
    netdata_url = os.environ.get("NETDATA_URL", "http://127.0.0.1:19999")
    source = ROOT / "config/netdata-grafana/prometheus.yml"
    with urllib.request.urlopen(netdata_url.rstrip("/") + "/api/v1/info", timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f"Netdata is unhealthy at {netdata_url}")
    with tempfile.TemporaryDirectory(prefix="netdata-grafana-smoke-") as tmp:
        work = Path(tmp)
        port = free_port()
        config = source.read_text().replace("127.0.0.1:19999", urllib.parse.urlsplit(netdata_url).netloc)
        config_path = work / "prometheus.yml"
        config_path.write_text(config)
        log_path = work / "prometheus.log"
        with log_path.open("w") as log:
            process = subprocess.Popen([prometheus, f"--config.file={config_path}", f"--storage.tsdb.path={work / 'data'}",
                                        f"--web.listen-address=127.0.0.1:{port}", "--storage.tsdb.retention.time=1h",
                                        "--storage.tsdb.retention.size=50MB", "--web.max-connections=8"],
                                       stdout=log, stderr=subprocess.STDOUT)
            base = f"http://127.0.0.1:{port}"
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Prometheus exited {process.returncode}: {log_path.read_text()[-4000:]}")
                    try:
                        with urllib.request.urlopen(base + "/-/ready", timeout=3) as response:
                            response.read()
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.25)
                else:
                    raise TimeoutError(f"Prometheus did not become ready: {log_path.read_text()[-4000:]}")
                expressions = dashboard_expressions()
                seen = {}
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    for expression in expressions:
                        query = urllib.parse.urlencode({"query": expression})
                        payload = get_json(base + "/api/v1/query?" + query)
                        result = payload.get("data", {}).get("result", [])
                        if result:
                            seen[expression] = result
                    if len(seen) == len(expressions):
                        break
                    time.sleep(1)
                if len(seen) != len(expressions):
                    raise AssertionError(f"Prometheus returned no data for expressions: {set(expressions) - set(seen)}")
                targets = get_json(base + "/api/v1/targets")['data']['activeTargets']
                target = next((t for t in targets if t["labels"].get("job") == "netdata"), None)
                assert target and target["health"] == "up", target
                assert target["scrapeUrl"].startswith(netdata_url.rstrip("/") + "/api/v1/allmetrics?"), target
                # Run the real provisioned Grafana backend against the real Netdata scrape.
                from security_dashboard_smoke import request, wait_for
                import secrets
                grafana = os.environ.get("GRAFANA_SERVER") or shutil.which("grafana-server")
                if not grafana:
                    raise RuntimeError("smoke requires the Grafana native binary")
                provisioning = work / "provisioning"
                for name in ("datasources", "dashboards"):
                    (provisioning / name).mkdir(parents=True)
                dashboards = work / "dashboards"
                dashboards.mkdir()
                (dashboards / "netdata.json").write_text((ROOT / "config/netdata-grafana/dashboard.json").read_text())
                (provisioning / "datasources/prometheus.yaml").write_text(
                    (ROOT / "config/netdata-grafana/provisioning/datasources/prometheus.yaml").read_text().replace("http://127.0.0.1:9090", base))
                (provisioning / "dashboards/netdata.yaml").write_text(
                    (ROOT / "config/netdata-grafana/provisioning/dashboards/netdata.yaml").read_text().replace("/etc/hermes-security-dashboard/netdata-dashboards", str(dashboards)))
                grafana_port = free_port()
                grafana_config = (ROOT / "config/security-dashboard/grafana.ini").read_text()
                grafana_config = grafana_config.replace("http_port = 3001", f"http_port = {grafana_port}")
                grafana_config = grafana_config.replace("/var/lib/hermes-security-dashboard/grafana", str(work / "grafana-data"))
                grafana_config = grafana_config.replace("/var/log/hermes-security-dashboard/grafana", str(work / "grafana-logs"))
                grafana_config = grafana_config.replace("/etc/hermes-security-dashboard/provisioning", str(provisioning))
                grafana_file = work / "grafana.ini"
                grafana_file.write_text(grafana_config)
                grafana_log = work / "grafana.log"
                password = secrets.token_hex(32)
                environment = os.environ | {"GF_SECURITY_ADMIN_PASSWORD": password, "GF_SECURITY_SECRET_KEY": secrets.token_hex(32)}
                with grafana_log.open("w") as output:
                    grafana_process = subprocess.Popen([grafana, f"--config={grafana_file}",
                        "--homepath=" + os.environ.get("GRAFANA_HOME", "/usr/share/grafana"), "--packaging=deb"],
                        cwd=work, stdout=output, stderr=subprocess.STDOUT, env=environment)
                    try:
                        grafana_url = f"http://127.0.0.1:{grafana_port}"
                        wait_for(grafana_url + "/api/health", grafana_process, grafana_log)
                        _, body = request(grafana_url + "/api/dashboards/uid/netdata-host", auth="admin:" + password)
                        dashboard = json.loads(body)["dashboard"]
                        for panel in dashboard["panels"]:
                            for expression in panel.get("targets", []):
                                query = expression | {"datasource": panel["datasource"], "intervalMs": 30000, "maxDataPoints": 100}
                                data = json.dumps({"queries": [query], "from": "now-5m", "to": "now"}).encode()
                                _, body = request(grafana_url + "/api/ds/query", data=data,
                                    headers={"Content-Type": "application/json"}, auth="admin:" + password)
                                result = json.loads(body)["results"][expression["refId"]]
                                assert not result.get("error") and result.get("frames"), (panel["title"], result)
                        _, body = request(grafana_url + "/api/datasources/uid/local-netdata-prometheus/health", auth="admin:" + password)
                        assert json.loads(body)["status"] == "OK"
                    finally:
                        grafana_process.terminate()
                        try:
                            grafana_process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            grafana_process.kill()
                            grafana_process.wait()
                print(f"Netdata→Prometheus→Grafana smoke PASS: real Netdata {netdata_url}, scrape up, {len(seen)} provisioned panel queries return data")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
