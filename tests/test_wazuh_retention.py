"""Retention CLI only selects recognized dated alerts, never arbitrary indices."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class RetentionTests(unittest.TestCase):
    def test_selects_only_approved_daily_indices_strictly_older_than_30_days(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "indices.json"
            fixture.write_text(json.dumps([
                {"index": "wazuh-alerts-4.x-2026.08.01"},
                {"index": "wazuh-alerts-4.x-2026.09.04"},
                {"index": "wazuh-alerts-4.x-2026.09.03"},
                {"index": "wazuh-monitoring-2026.01.01"},
                {"index": ".opendistro_security"},
                {"index": "wazuh-states-vulnerabilities-hermes-host"},
                {"index": "wazuh-alerts-4.x-2026.02.31"},
                {"index": "other-2026.01.01"}]))
            result = subprocess.run(["/usr/bin/python3", str(REPO / "scripts/wazuh-retention.py"),
                                     "--plan", str(fixture), "--today", "2026-10-04"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ["wazuh-alerts-4.x-2026.08.01", "wazuh-alerts-4.x-2026.09.03"])


if __name__ == "__main__":
    unittest.main()
