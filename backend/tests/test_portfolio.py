
"""MVP 가상 정책 조합 계산 회귀 테스트."""

import json
import itertools
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from benefitfinder.domain.portfolio import (
    CalculationLimitExceeded,
    calculate_portfolios,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "mvp_planning_rules.json"


def _brute_force_portfolios(
    eligible: list[str],
    amounts: dict[str, int],
    conflicts: list[list[str]],
    unverified_pairs: list[tuple[str, str]],
    limit: int = 3,
) -> list[dict]:
    """Small-input oracle that deliberately enumerates every subset."""

    policies = sorted(set(eligible))
    conflict_pairs = {frozenset(pair) for pair in conflicts}
    unverified = {frozenset(pair) for pair in unverified_pairs}
    compatible = []

    for size in range(len(policies) + 1):
        for members in itertools.combinations(policies, size):
            member_set = set(members)
            if any(pair <= member_set for pair in conflict_pairs):
                continue
            compatible.append(frozenset(members))

    maximal = [
        members
        for members in compatible
        if not any(members < other for other in compatible)
    ]
    maximal.sort(
        key=lambda members: (
            -sum(amounts[policy] for policy in members),
            sum(1 for pair in unverified if pair <= members),
            len(members),
            tuple(sorted(members)),
        )
    )

    return [
        {
            "rank": index,
            "members": sorted(members),
            "total_won": sum(amounts[policy] for policy in members),
        }
        for index, members in enumerate(maximal[:limit], start=1)
    ]


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


class PortfolioSearchTest(unittest.TestCase):
    def test_empty_and_duplicate_inputs(self):
        self.assertEqual(calculate_portfolios([], {}, []), [])
        self.assertEqual(
            calculate_portfolios(
                [],
                {},
                [],
                max_search_nodes=0,
                timeout_seconds=0,
            ),
            [],
        )

        actual = calculate_portfolios(
            eligible=["B", "A", "A"],
            amounts={"A": 100, "B": 200},
            conflicts=[["A", "B"], ["B", "A"]],
            unverified_pairs=[["A", "B"], ["B", "A"]],
        )

        self.assertEqual(
            actual,
            [
                {"rank": 1, "members": ["B"], "total_won": 200},
                {"rank": 2, "members": ["A"], "total_won": 100},
            ],
        )

    def test_all_compatible_thirty_policies_finish_in_one_path(self):
        policies = [f"P{index:02d}" for index in range(30)]
        amounts = {policy: index + 1 for index, policy in enumerate(policies)}

        actual = calculate_portfolios(
            eligible=policies,
            amounts=amounts,
            conflicts=[],
            max_search_nodes=31,
        )

        self.assertEqual(
            actual,
            [{"rank": 1, "members": policies, "total_won": sum(amounts.values())}],
        )

    def test_node_limit_raises_without_partial_results(self):
        policies = [f"P{index:02d}" for index in range(30)]
        amounts = {policy: 1 for policy in policies}

        with self.assertRaises(CalculationLimitExceeded) as context:
            calculate_portfolios(
                eligible=policies,
                amounts=amounts,
                conflicts=[],
                max_search_nodes=30,
            )

        message = str(context.exception).lower()
        self.assertIn("max_search_nodes", message)
        self.assertNotIn("profile", message)

    def test_time_limit_is_checked_during_search(self):
        with patch(
            "benefitfinder.domain.portfolio.time.monotonic",
            side_effect=[0.0, 0.0, 0.5],
        ):
            with self.assertRaises(CalculationLimitExceeded) as context:
                calculate_portfolios(
                    eligible=["A", "B"],
                    amounts={"A": 1, "B": 1},
                    conflicts=[],
                    timeout_seconds=0.4,
                )

        self.assertIn("timeout_seconds", str(context.exception).lower())

    def test_twenty_first_compatible_policy_is_not_truncated(self):
        incompatible = [f"P{index:02d}" for index in range(20)]
        policies = [*incompatible, "Z"]
        amounts = {
            **{policy: 1_000_000 for policy in incompatible},
            "Z": 990_000,
        }
        conflicts = [list(pair) for pair in itertools.combinations(incompatible, 2)]

        actual = calculate_portfolios(
            eligible=policies,
            amounts=amounts,
            conflicts=conflicts,
            max_search_nodes=1_000,
        )

        self.assertEqual(len(actual), 3)
        self.assertEqual(actual[0]["members"], ["P00", "Z"])
        self.assertEqual(actual[0]["total_won"], 1_990_000)

    def test_unverified_pair_count_precedes_size_and_lexicographic_order(self):
        policies = ["A", "B", "C", "D"]
        conflicts = [["A", "B"], ["B", "C"], ["C", "D"]]

        actual = calculate_portfolios(
            eligible=policies,
            amounts={policy: 100 for policy in policies},
            conflicts=conflicts,
            unverified_pairs=[("A", "C"), ("C", "A")],
        )

        self.assertEqual(
            [portfolio["members"] for portfolio in actual],
            [["A", "D"], ["B", "D"], ["A", "C"]],
        )

    def test_fewer_policies_and_lexicographic_order_break_remaining_ties(self):
        policies = ["A", "B", "C", "D"]
        conflicts = [["A", "B"], ["A", "C"], ["A", "D"]]

        fewer_policies = calculate_portfolios(
            eligible=policies,
            amounts={"A": 300, "B": 100, "C": 100, "D": 100},
            conflicts=conflicts,
        )
        self.assertEqual(
            [portfolio["members"] for portfolio in fewer_policies],
            [["A"], ["B", "C", "D"]],
        )

        lexicographic = calculate_portfolios(
            eligible=policies,
            amounts={policy: 100 for policy in policies},
            conflicts=[["A", "B"], ["B", "C"], ["C", "D"]],
        )
        self.assertEqual(
            [portfolio["members"] for portfolio in lexicographic],
            [["A", "C"], ["A", "D"], ["B", "D"]],
        )

    def test_matches_brute_force_oracle_on_small_graphs(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                randomizer = random.Random(seed)
                policies = [f"P{index}" for index in range(7)]
                amounts = {
                    policy: randomizer.randint(1, 9) * 1_000
                    for policy in policies
                }
                pairs = list(itertools.combinations(policies, 2))
                conflicts = [
                    list(pair)
                    for pair in pairs
                    if randomizer.random() < 0.35
                ]
                unverified_pairs = [
                    pair
                    for pair in pairs
                    if randomizer.random() < 0.2
                ]

                actual = calculate_portfolios(
                    eligible=policies,
                    amounts=amounts,
                    conflicts=conflicts,
                    unverified_pairs=unverified_pairs,
                    max_search_nodes=10_000,
                )
                expected = _brute_force_portfolios(
                    eligible=policies,
                    amounts=amounts,
                    conflicts=conflicts,
                    unverified_pairs=unverified_pairs,
                )

                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
