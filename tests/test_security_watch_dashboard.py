import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SecurityWatchDashboardTest(unittest.TestCase):
    def test_dashboard_has_watch_panels_and_loki_queries(self):
        dashboard = json.loads((ROOT / "config/security-watch/dashboard.json").read_text())
        self.assertEqual(dashboard["uid"], "server-watch")
        self.assertEqual(dashboard["refresh"], "10s")
        self.assertEqual(dashboard["time"]["from"], "now-15m")
        self.assertGreaterEqual(len(dashboard["panels"]), 10)
        self.assertLessEqual(len(dashboard["panels"]), 14)
        panels = {panel["title"]: panel for panel in dashboard["panels"]}
        health_exprs = [target["expr"] for title in ("Collector health counters (latest reported cumulative values)", "Collector heartbeat — stale/idle is not green") for target in panels[title]["targets"]]
        self.assertTrue(all('| json kind="kind"' in expr for expr in health_exprs))
        self.assertFalse(any("| json |" in expr for expr in health_exprs))
        expressions = "\n".join(target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", []))
        self.assertIn("${source_ip:raw}", expressions)
        self.assertIn('src_ip=~`${source_ip:raw}`', expressions)
        self.assertNotIn("${source_ip:json}", expressions)
        self.assertNotIn("${source_ip:doublequote}", expressions)
        self.assertNotIn("${source_ip:regex}", expressions)
        self.assertEqual(panels["Observed flows (not attacks)"]["targets"][0]["queryType"], "instant")
        self.assertEqual(panels["Distinct observed source IPs"]["targets"][0]["queryType"], "instant")
        self.assertIn("count(sum by (src_ip)", panels["Distinct observed source IPs"]["targets"][0]["expr"])
        geomap = panels["Approximate source geolocation (emitted flows)"]
        self.assertEqual([t["id"] for t in geomap["transformations"]], ["labelsToFields", "convertFieldType"])
        self.assertEqual({c["targetField"]: c["destinationType"] for c in geomap["transformations"][1]["options"]["conversions"]},
                         {"latitude": "number", "longitude": "number"})
        self.assertEqual(panels["Top observed source IPs (approximate attribution)"]["transformations"][0]["id"], "labelsToFields")
        for value in ("job=\"security-watch\"", "attempt", "health", "src_ip", "dst_port", "latitude", "longitude", "packets_dropped"):
            with self.subTest(value=value):
                self.assertIn(value, expressions)
        text = " ".join(panel.get("options", {}).get("content", "") for panel in dashboard["panels"] if panel["type"] == "text")
        for phrase in ("DB-IP", "CC BY 4.0", "unknown", "UDP", "approximate", "Host Security"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), text.lower())

    def test_watch_pipeline_is_allowlisted_and_isolated_from_existing_pipeline(self):
        config = (ROOT / "config/security-dashboard/config.alloy").read_text()
        baseline = config.split('\n// Server Watch journal reader', 1)[0]
        # Pin the reviewed original bytes, independent of HEAD and shallow CI history.
        self.assertEqual(__import__("hashlib").sha256(baseline.encode()).hexdigest(),
                         "5e4f0e91604eaa7b930ff7f03dae98e27dd78b1cb61024a8dd86a61defcbae43")
        self.assertIn('_SYSTEMD_UNIT=hermes-security-watch.service', config)
        self.assertIn('job = "security-watch"', config)
        self.assertIn("drop_malformed = true", config)
        self.assertIn('kind                  = "kind"', config)
        self.assertIn('src_ip                = "src_ip"', config)
        self.assertIn('packets_dropped       = "packets_dropped"', config)
        self.assertIn("toJson", config)
        self.assertIn('values = ["watch_kind", "watch_schema"]', config)
        self.assertIn('values = ["job", "host"]', config)
        self.assertIn('selector = "{job=\\"security-watch\\",watch_schema!=\\"1\\"}"', config)
        self.assertNotIn('| json | schema !=', config)
        self.assertNotRegex(config, r'labels\s*=\s*\{[^}]*\b(?:src_ip|dst_ip|source_ip)\b')

    def test_dashboard_provisioning_points_to_dashboard_file(self):
        provision = (ROOT / "config/security-watch/provisioning.yaml").read_text()
        self.assertIn("name: Server Watch", provision)
        self.assertIn("path: /etc/hermes-security-dashboard/watch-dashboards", provision)


if __name__ == "__main__":
    unittest.main()
