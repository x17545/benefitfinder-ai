
"""MVP 가상 정책 조합 계산 회귀 테스트."""

import json
import unittest
from pathlib import Path

from benefitfinder.domain.portfolio import calculate_portfolios


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "mvp_planning_rules.json"


class PortfolioMvpTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_mvp_scenarios(self):
        policies = {
            policy["id"]: policy
            for policy in self.data["policies"]
        }

        period_keys = {
            "y1": "y1",
            "y2": "y2",
            "full": "full",
        }

        for scenario in self.data["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                period = period_keys[scenario["period"]]

                amounts = {
                    policy_id: policies[policy_id]["amounts_won"][period]
                    for policy_id in scenario["eligible"]
                }

                actual = calculate_portfolios(
                    eligible=scenario["eligible"],
                    amounts=amounts,
                    conflicts=self.data["conflicts"],
                )

                expected = scenario["expected"]

                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
