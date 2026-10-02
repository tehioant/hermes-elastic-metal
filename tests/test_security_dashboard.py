import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SecurityDashboardConfigTest(unittest.TestCase):
    def test_alloy_collects_only_selected_sources_and_uses_low_cardinality_labels(self):
        config = (ROOT / "config/security-dashboard/config.alloy").read_text()
        for source in ("_COMM=sshd", "/var/log/fail2ban.log", "ufw", "caddy"):
            with self.subTest(source=source):
                self.assertIn(source, config)
        self.assertNotIn("journalctl", config)
        self.assertIn('values = ["filename", "service_name", "detected_level"]', config)
        self.assertNotRegex(config, r"(?i)label\s*=\s*\"(?:remote_addr|client_ip|path|uri)\"")

    def test_loki_is_loopback_only_and_retains_seven_days(self):
        config = (ROOT / "config/security-dashboard/loki.yaml").read_text()
        self.assertIn("http_listen_address: 127.0.0.1", config)
        self.assertIn("http_listen_port: 3100", config)
        self.assertIn("retention_period: 168h", config)
        self.assertIn("retention_enabled: true", config)

    def test_loki_storage_paths_match_its_service_sandbox(self):
        config = (ROOT / "config/security-dashboard/loki.yaml").read_text()
        unit = (ROOT / "systemd/hermes-security-loki.service").read_text()
        self.assertIn("ReadWritePaths=/var/lib/hermes-security-dashboard/loki", unit)
        self.assertNotIn("/var/lib/loki", config)
        self.assertIn("path_prefix: /var/lib/hermes-security-dashboard/loki", config)

    def test_ssh_collection_includes_current_openssh_session_process(self):
        config = (ROOT / "config/security-dashboard/config.alloy").read_text()
        self.assertIn('_COMM=sshd-session', config)

    def test_dashboard_provisions_loki_and_has_investigation_panels(self):
        dashboard = json.loads((ROOT / "config/security-dashboard/dashboard.json").read_text())
        panels = dashboard["panels"]
        self.assertGreaterEqual(len(panels), 4)
        expressions = "\n".join(target["expr"] for panel in panels for target in panel.get("targets", []))
        for expression in ("{job=\"ufw\"}", "{job=\"ssh\"}", "{job=\"fail2ban\"}", "{job=\"caddy\"}"):
            with self.subTest(expression=expression):
                self.assertIn(expression, expressions)
        provision = (ROOT / "config/security-dashboard/provisioning/datasources/loki.yaml").read_text()
        self.assertIn("type: loki", provision)
        self.assertIn("url: http://127.0.0.1:3100", provision)


if __name__ == "__main__":
    unittest.main()
