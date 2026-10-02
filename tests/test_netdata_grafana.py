import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NetdataGrafanaConfigTest(unittest.TestCase):
    def test_prometheus_scrapes_only_bounded_average_netdata_gauges_on_loopback(self):
        config = (ROOT / "config/netdata-grafana/prometheus.yml").read_text()
        self.assertIn("metrics_path: /api/v1/allmetrics", config)
        self.assertIn("targets: [127.0.0.1:19999]", config)
        self.assertIn("source: [average]", config)
        self.assertIn("server: [hermes-netdata-grafana]", config)
        self.assertIn("scrape_interval: 30s", config)
        self.assertIn("sample_limit: 1000", config)
        self.assertNotRegex(config, r"\b(?:rate|irate)\(")

    def test_dashboard_has_local_prometheus_queries_and_only_real_netdata_dimensions(self):
        dashboard = json.loads((ROOT / "config/netdata-grafana/dashboard.json").read_text())
        self.assertEqual(dashboard["uid"], "netdata-host")
        self.assertEqual(dashboard["title"], "Netdata Host")
        panels = dashboard["panels"]
        self.assertGreaterEqual(len(panels), 6)
        expressions = "\n".join(t["expr"] for p in panels for t in p.get("targets", []))
        for metric in ("netdata_system_cpu_percentage_average", "netdata_system_ram_MiB_average",
                       "netdata_disk_space_GiB_average", "netdata_system_load_load_average",
                       "netdata_net_net_kilobits_persec_average"):
            self.assertIn(metric, expressions)
        provisioning = (ROOT / "config/netdata-grafana/provisioning/datasources/prometheus.yaml").read_text()
        self.assertIn("local-netdata-prometheus", provisioning)
        self.assertIn("http://127.0.0.1:9090", provisioning)
        self.assertIn("isDefault: false", provisioning)

    def test_preflight_requires_managed_security_stack_without_mutating_fake_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "var/lib/hermes-security-dashboard/managed-by-hermes"
            marker.parent.mkdir(parents=True)
            marker.write_text("hermes-security-dashboard-managed-v1" + chr(10))
            env = os.environ | {"NETDATA_GRAFANA_ROOT": directory}
            result = subprocess.run(["bash", str(ROOT / "scripts/install-netdata-grafana.sh"), "--preflight"],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list((root / "var/lib/hermes-security-dashboard").iterdir()), [marker])
            unmanaged = root / "etc/hermes-netdata-metrics"
            unmanaged.mkdir(parents=True)
            refused = subprocess.run(["bash", str(ROOT / "scripts/install-netdata-grafana.sh"), "--preflight"],
                                     env=env, text=True, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("existing Netdata metrics state is unmanaged", refused.stderr)
            self.assertTrue(unmanaged.is_dir())

    def test_preflight_refuses_each_unmanaged_grafana_target_without_changing_bytes(self):
        targets = (
            "etc/hermes-security-dashboard/provisioning/datasources/netdata-prometheus.yaml",
            "etc/hermes-security-dashboard/provisioning/dashboards/netdata.yaml",
            "etc/hermes-security-dashboard/netdata-dashboards/netdata.json",
        )
        for relative in targets:
            with self.subTest(target=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "var/lib/hermes-security-dashboard/managed-by-hermes"
                marker.parent.mkdir(parents=True)
                marker.write_text("hermes-security-dashboard-managed-v1\n")
                target = root / relative
                target.parent.mkdir(parents=True)
                original = b"operator-owned exact bytes\x00\xff\n"
                target.write_bytes(original)
                env = os.environ | {"NETDATA_GRAFANA_ROOT": directory}
                result = subprocess.run(["bash", str(ROOT / "scripts/install-netdata-grafana.sh"), "--preflight"],
                                        env=env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unmanaged", result.stderr)
                self.assertEqual(target.read_bytes(), original)

    def test_preflight_allows_managed_rerun_and_rejects_invalid_bridge_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            security_marker = root / "var/lib/hermes-security-dashboard/managed-by-hermes"
            security_marker.parent.mkdir(parents=True)
            security_marker.write_text("hermes-security-dashboard-managed-v1\n")
            state = root / "var/lib/hermes-netdata-metrics"
            state.mkdir(parents=True)
            bridge_marker = state / "managed-by-hermes"
            bridge_marker.write_text("netdata-prometheus-managed-v1\n")
            targets = (
                "etc/hermes-security-dashboard/provisioning/datasources/netdata-prometheus.yaml",
                "etc/hermes-security-dashboard/provisioning/dashboards/netdata.yaml",
                "etc/hermes-security-dashboard/netdata-dashboards/netdata.json",
            )
            for relative in targets:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"previously managed bytes\n")
            env = os.environ | {"NETDATA_GRAFANA_ROOT": directory}
            allowed = subprocess.run(["bash", str(ROOT / "scripts/install-netdata-grafana.sh"), "--preflight"],
                                      env=env, text=True, capture_output=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            bridge_marker.write_text("invalid\n")
            refused = subprocess.run(["bash", str(ROOT / "scripts/install-netdata-grafana.sh"), "--preflight"],
                                     env=env, text=True, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("ownership marker is invalid", refused.stderr)

    def test_bridge_marker_is_root_owned_outside_prometheus_writable_tsdb_child(self):
        installer = (ROOT / "scripts/install-netdata-grafana.sh").read_text()
        unit = (ROOT / "systemd/hermes-netdata-prometheus.service").read_text()
        self.assertIn('install -d -o root -g root -m 0755 "${STATE_DIR}"', installer)
        self.assertIn('install -d -o prometheus -g prometheus -m 0750 "${STATE_DIR}/prometheus"', installer)
        self.assertIn('chmod 0600 "${MARKER}"', installer)
        self.assertIn("--storage.tsdb.path=/var/lib/hermes-netdata-metrics/prometheus", unit)
        self.assertIn("ReadWritePaths=/var/lib/hermes-netdata-metrics/prometheus", unit)

    def test_prometheus_unit_is_resource_bounded_and_loopback_only(self):
        unit = (ROOT / "systemd/hermes-netdata-prometheus.service").read_text()
        for setting in ("User=prometheus", "--web.listen-address=127.0.0.1:9090",
                        "--storage.tsdb.retention.time=7d", "--storage.tsdb.retention.size=1GB",
                        "CPUQuota=50%", "MemoryMax=512M", "ProtectSystem=strict"):
            self.assertIn(setting, unit)

    def test_new_package_stock_prometheus_is_disabled_then_masked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._run_package_fixture(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "systemctl.log").read_text().splitlines(),
                             ["disable --now prometheus.service", "mask prometheus.service"])
            self.assertEqual((root / "apt.log").read_text().strip(),
                             "install -y --no-install-recommends prometheus")
            self.assertFalse((root / "usr/sbin/policy-rc.d").exists())

    def _run_package_fixture(self, root, apt_status=0, failed_action="", apt_create_binary=True):
        (root / "var/lib/hermes-netdata-metrics").mkdir(parents=True, exist_ok=True)
        (root / "usr/sbin").mkdir(parents=True, exist_ok=True)
        env = os.environ | {
            "NETDATA_GRAFANA_ROOT": str(root), "PROBE": str(root),
            "APT_STATUS": str(apt_status), "FAILED_ACTION": failed_action,
            "APT_CREATE_BINARY": str(apt_create_binary).lower(),
        }
        script = '''
source "$1"
command() {
  if [[ "$1" == -v && "${2:-}" == prometheus ]]; then
    [[ -f "$PROBE/prometheus-binary" ]]
  else
    builtin command "$@"
  fi
}
apt-get() {
  printf '%s\\n' "$*" >> "$PROBE/apt.log"
  if [[ "$APT_CREATE_BINARY" == true ]]; then
    printf 'installed\\n' > "$PROBE/prometheus-binary"
    printf 'enabled\\n' > "$PROBE/stock-state"
  fi
  return "$APT_STATUS"
}
systemctl() {
  printf '%s\\n' "$*" >> "$PROBE/systemctl.log"
  [[ "$1" != "$FAILED_ACTION" ]] || return 1
  if [[ "$1" == mask ]]; then printf 'masked\\n' > "$PROBE/stock-state"; fi
}
ensure_prometheus_package
'''
        return subprocess.run(
            ["bash", "-c", script, "test", str(ROOT / "scripts/install-netdata-grafana.sh")],
            env=env, text=True, capture_output=True,
        )

    def test_partial_package_failure_retains_ownership_and_masks_stock_service_on_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._run_package_fixture(root, apt_status=100)
            self.assertNotEqual(first.returncode, 0)
            self.assertIn("failed to install Prometheus package (exit 100)", first.stderr)
            package_marker = root / "var/lib/hermes-netdata-metrics/prometheus-package-managed"
            self.assertEqual(package_marker.read_text(), "netdata-prometheus-package-managed-v1\n")
            self.assertEqual(package_marker.stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / "usr/sbin/policy-rc.d").exists())
            self.assertEqual((root / "stock-state").read_text().strip(), "masked")
            (root / "systemctl.log").unlink()
            (root / "stock-state").write_text("enabled\n")
            retry = self._run_package_fixture(root)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual((root / "systemctl.log").read_text().splitlines(),
                             ["disable --now prometheus.service", "mask prometheus.service"])
            self.assertEqual((root / "stock-state").read_text().strip(), "masked")
            self.assertEqual(len((root / "apt.log").read_text().splitlines()), 1)

    def test_failed_package_attempt_without_binary_discards_marker_before_foreign_install_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_marker = root / "var/lib/hermes-netdata-metrics/prometheus-package-managed"
            failed = self._run_package_fixture(root, apt_status=100, apt_create_binary=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("failed to install Prometheus package (exit 100)", failed.stderr)
            self.assertFalse((root / "prometheus-binary").exists())
            self.assertFalse(package_marker.exists())

            # An independent package transaction installs Prometheus afterward.
            (root / "prometheus-binary").write_text("foreign install\n")
            (root / "stock-state").write_text("operator-managed\n")
            (root / "systemctl.log").unlink(missing_ok=True)
            retry = self._run_package_fixture(root)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual((root / "stock-state").read_text(), "operator-managed\n")
            self.assertFalse((root / "systemctl.log").exists())
            self.assertFalse(package_marker.exists())

    def test_invalid_package_ownership_marker_refuses_service_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "var/lib/hermes-netdata-metrics"
            state.mkdir(parents=True)
            (state / "prometheus-package-managed").write_text("invalid\n")
            (root / "prometheus-binary").write_text("pre-existing\n")
            result = self._run_package_fixture(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Prometheus package ownership marker is invalid", result.stderr)
            self.assertFalse((root / "systemctl.log").exists())
            self.assertFalse((root / "apt.log").exists())

    def test_preexisting_prometheus_without_package_marker_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prometheus-binary").write_text("pre-existing\n")
            (root / "stock-state").write_text("operator-managed\n")
            result = self._run_package_fixture(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "stock-state").read_text(), "operator-managed\n")
            self.assertFalse((root / "systemctl.log").exists())
            self.assertFalse((root / "apt.log").exists())
            self.assertFalse((root / "var/lib/hermes-netdata-metrics/prometheus-package-managed").exists())

    def test_package_service_protection_failures_abort_and_remain_retryable(self):
        for action in ("disable", "mask"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                failed = self._run_package_fixture(root, failed_action=action)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn(f"failed to {action} stock prometheus.service", failed.stderr)
                self.assertFalse((root / "usr/sbin/policy-rc.d").exists())
                expected = ["disable --now prometheus.service"]
                if action == "mask":
                    expected.append("mask prometheus.service")
                self.assertEqual((root / "systemctl.log").read_text().splitlines(), expected)
                (root / "systemctl.log").unlink()
                retried = self._run_package_fixture(root)
                self.assertEqual(retried.returncode, 0, retried.stderr)
                self.assertEqual((root / "stock-state").read_text().strip(), "masked")
                self.assertEqual((root / "systemctl.log").read_text().splitlines(),
                                 ["disable --now prometheus.service", "mask prometheus.service"])

    def test_package_install_preserves_foreign_policy_without_claiming_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "usr/sbin/policy-rc.d"
            policy.parent.mkdir(parents=True)
            original = b"operator-owned policy\n"
            policy.write_bytes(original)
            result = self._run_package_fixture(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exists; inspect before installing packages", result.stderr)
            self.assertEqual(policy.read_bytes(), original)
            self.assertFalse((root / "var/lib/hermes-netdata-metrics/prometheus-package-managed").exists())
            self.assertFalse((root / "apt.log").exists())

    def test_installer_is_opt_in_and_never_restarts_existing_security_services(self):
        installer = (ROOT / "scripts/install-netdata-grafana.sh").read_text()
        self.assertIn("--preflight", installer)
        self.assertIn("netdata-prometheus-managed-v1", installer)
        self.assertIn("systemctl restart hermes-security-grafana.service", installer)
        self.assertNotIn("systemctl restart grafana-server", installer)

    def test_smoke_uses_exact_provisioned_dashboard_expressions(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("netdata_smoke", ROOT / "tests/netdata_grafana_smoke.py")
        assert spec is not None and spec.loader is not None
        smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoke)
        dashboard = json.loads((ROOT / "config/netdata-grafana/dashboard.json").read_text())
        expected = [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
        self.assertEqual(smoke.dashboard_expressions(), expected)

    def test_smoke_verifies_dashboard_queries_through_real_grafana(self):
        smoke = (ROOT / "tests/netdata_grafana_smoke.py").read_text()
        self.assertIn("/api/ds/query", smoke)
        self.assertIn("/api/dashboards/uid/netdata-host", smoke)
        self.assertIn("--config=", smoke)

    def test_smoke_script_queries_real_netdata_and_provisioned_panel_expressions(self):
        smoke = (ROOT / "tests/netdata_grafana_smoke.py").read_text()
        self.assertIn("NETDATA_URL", smoke)
        self.assertIn("--config.file=", smoke)
        self.assertIn("/api/v1/query", smoke)


if __name__ == "__main__":
    unittest.main()
