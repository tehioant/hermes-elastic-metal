import sys
import unittest
import subprocess
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment_guards import verify_state, verify_plan


class DeploymentGuardsTest(unittest.TestCase):
    def test_rejects_state_without_the_existing_server(self):
        with self.assertRaisesRegex(ValueError, "server"):
            verify_state({"lineage": "existing", "resources": []})
    def test_rejects_state_without_lineage(self):
        with self.assertRaisesRegex(ValueError, "lineage"):
            verify_state({"resources": [{"type": "scaleway_baremetal_server", "name": "this", "instances": [{}]}]})

    def test_rejects_state_with_an_unprovisioned_server(self):
        with self.assertRaisesRegex(ValueError, "server"):
            verify_state({"lineage": "existing", "resources": [{"type": "scaleway_baremetal_server", "name": "this", "instances": []}]})

    def test_rejects_state_without_backup_bucket(self):
        with self.assertRaisesRegex(ValueError, "backup bucket"):
            verify_state({"lineage": "existing", "resources": [{"type": "scaleway_baremetal_server", "name": "this", "instances": [{}]}]})

    def test_accepts_migrated_existing_state(self):
        verify_state({"lineage": "existing", "resources": [
            {"type": "scaleway_baremetal_server", "name": "this", "instances": [{}]},
            {"type": "scaleway_object_bucket", "name": "backup", "instances": [{}]},
        ]})

    def test_rejects_destructive_plan(self):
        with self.assertRaisesRegex(ValueError, "delete"):
            verify_plan({"complete": True, "errored": False, "applyable": True, "resource_changes": [
                {"address": "scaleway_object_bucket.backup", "change": {"actions": ["delete"]}},
            ]})

    def test_accepts_noop_plan_without_resource_changes(self):
        verify_plan({"complete": True, "errored": False, "applyable": False})

    def test_rejects_malformed_plan(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            verify_plan({})

    def test_rejects_plan_change_without_actions(self):
        with self.assertRaisesRegex(ValueError, "actions"):
            verify_plan({"complete": True, "errored": False, "applyable": True,
                         "resource_changes": [{"address": "scaleway_baremetal_server.this", "change": {}}]})

    def test_cli_accepts_existing_state_without_printing_it(self):
        state = {"lineage": "secret-lineage", "resources": [
            {"type": "scaleway_baremetal_server", "name": "this", "instances": [{}]},
            {"type": "scaleway_object_bucket", "name": "backup", "instances": [{}]},
        ]}
        env = os.environ.copy()
        env["EXPECTED_STATE_LINEAGE"] = "secret-lineage"
        result = subprocess.run([sys.executable, "scripts/deployment_guards.py", "state"],
                                input=json.dumps(state), text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("State verified", result.stdout)
        self.assertNotIn("secret-lineage", result.stdout + result.stderr)
    def test_cli_rejects_state_without_expected_lineage(self):
        state = {"lineage": "existing", "resources": [
            {"type": "scaleway_baremetal_server", "name": "this", "instances": [{}]},
            {"type": "scaleway_object_bucket", "name": "backup", "instances": [{}]},
        ]}
        env = os.environ.copy()
        env.pop("EXPECTED_STATE_LINEAGE", None)
        result = subprocess.run([sys.executable, "scripts/deployment_guards.py", "state"],
                                input=json.dumps(state), text=True, capture_output=True, env=env)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_rejects_different_lineage(self):
        state = {"lineage": "other", "resources": [
            {"type": "scaleway_baremetal_server", "name": "this", "instances": [{}]},
            {"type": "scaleway_object_bucket", "name": "backup", "instances": [{}]},
        ]}
        env = os.environ.copy()
        env["EXPECTED_STATE_LINEAGE"] = "existing"
        result = subprocess.run([sys.executable, "scripts/deployment_guards.py", "state"],
                                input=json.dumps(state), text=True, capture_output=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("other", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
