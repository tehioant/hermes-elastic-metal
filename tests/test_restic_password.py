import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_restic_password.sh"


class ResticPasswordTest(unittest.TestCase):
    def test_rejects_a_different_password_before_changing_existing_backup_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "password"
            path.write_text("existing\n")
            env = os.environ.copy()
            env["RESTIC_PASSWORD"] = "replacement"
            result = subprocess.run(["bash", str(SCRIPT), str(path)],
                                    capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(), "existing\n")
            self.assertNotIn("replacement", result.stdout + result.stderr)

    def test_accepts_matching_existing_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "password"
            path.write_text("existing\n")
            env = os.environ.copy()
            env["RESTIC_PASSWORD"] = "existing"
            result = subprocess.run(["bash", str(SCRIPT), str(path)],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_initial_setup_without_a_password_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["RESTIC_PASSWORD"] = "new"
            result = subprocess.run(["bash", str(SCRIPT), str(Path(directory) / "password")],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
