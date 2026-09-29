from pathlib import Path
import unittest


class HighRiskAuthorizationPolicyTest(unittest.TestCase):
    def test_high_risk_approval_requires_exact_instruction(self):
        skill = Path(__file__).resolve().parents[1].joinpath("SKILL.md").read_text()
        row = next(line for line in skill.splitlines() if line.startswith("| 13 |"))
        self.assertIn("本条具体审批", row)
        self.assertNotIn("| 同意 | `proposal`（approve） |", row)


if __name__ == "__main__":
    unittest.main()
