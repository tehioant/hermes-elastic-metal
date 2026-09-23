import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync_ops_keys.sh"


class OpsKeysTest(unittest.TestCase):
    def test_preserves_explicit_deploy_key_across_syncs_and_revokes_on_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_keys = root / "root.keys"
            deploy_key = root / "deploy.pub"
            ops_keys = root / "ops.keys"
            root_keys.write_text('command="console-only" ssh-ed25519 AAAAROOT person\n')
            deploy_key.write_text('restrict ssh-ed25519 AAAADEPLOY ci\n')

            for _ in range(2):
                result = subprocess.run(["bash", str(SCRIPT), str(root_keys), str(deploy_key), str(ops_keys)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(ops_keys.read_text().splitlines(),
                                 ["ssh-ed25519 AAAAROOT person", "restrict ssh-ed25519 AAAADEPLOY ci"])

            deploy_key.unlink()
            result = subprocess.run(["bash", str(SCRIPT), str(root_keys), str(deploy_key), str(ops_keys)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ops_keys.read_text(), "ssh-ed25519 AAAAROOT person\n")


if __name__ == "__main__":
    unittest.main()
