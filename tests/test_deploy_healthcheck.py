import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml"
UNITS = ("docker.service", "restic-backup.timer", "healthcheck.timer")


class DeployHealthcheckTest(unittest.TestCase):
    def test_inactive_unit_fails_deployment_even_when_others_are_active(self):
        workflow = WORKFLOW.read_text()
        remote_commands = re.findall(r"ssh \"ops@\$\{TAILSCALE_HOST\}\" '([^']*systemctl[^']*)'", workflow)
        self.assertEqual(len(remote_commands), 1)

        with tempfile.TemporaryDirectory() as directory:
            commands = Path(directory)
            sudo = commands / "sudo"
            sudo.write_text('#!/usr/bin/env bash\n[[ "$1" == "-n" ]] && shift\nexec "$@"\n')
            sudo.chmod(0o755)
            systemctl = commands / "systemctl"
            systemctl.write_text('''#!/usr/bin/env bash
[[ "$1" == "is-active" && "$2" == "--quiet" ]] || exit 2
shift 2
for unit in "$@"; do
  if [[ "$unit" != "$FAILED_UNIT" ]]; then exit 0; fi
done
exit 3
''')
            systemctl.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{commands}:{env['PATH']}"
            for failed_unit in UNITS:
                with self.subTest(failed_unit=failed_unit):
                    env["FAILED_UNIT"] = failed_unit
                    result = subprocess.run(["bash", "-c", remote_commands[0]], env=env,
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0, f"{failed_unit} went unnoticed")

            env["FAILED_UNIT"] = "none"
            result = subprocess.run(["bash", "-c", remote_commands[0]], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
