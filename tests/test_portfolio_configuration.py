"""Check that merge-triggered DNS activation is configured in reviewed code."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PortfolioConfigurationTest(unittest.TestCase):
    def test_production_pages_target_is_configured_in_terraform(self):
        variables = (ROOT / "terraform/variables.tf").read_text()
        block = re.search(
            r'(?ms)^variable "portfolio_pages_hostname" \{(.*?)^\}', variables
        )
        if block is None:
            self.fail("The root Pages hostname variable is missing.")
        target = re.search(r'\bdefault\s*=\s*"([^"]*)"', block.group(1))
        if target is None:
            self.fail("The root Pages hostname variable must have a default.")
        self.assertEqual(target.group(1), "orbit-portfolio.pages.dev")

    def test_ci_and_cd_do_not_override_the_reviewed_target_with_an_empty_variable(self):
        for name in ("ci.yml", "cd.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text()
                self.assertNotRegex(
                    workflow, r"(?m)^\s+TF_VAR_portfolio_pages_hostname\s*:"
                )

    def test_production_module_receives_the_root_target(self):
        portfolio = (ROOT / "terraform/portfolio.tf").read_text()
        self.assertRegex(
            portfolio,
            r"portfolio_pages_hostname\s*=\s*var\.portfolio_pages_hostname\b",
        )


if __name__ == "__main__":
    unittest.main()
