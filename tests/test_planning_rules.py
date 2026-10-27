"""Virtual planning-answer regression tests (fixture oracle checks).

These tests verify tests/fixtures/planning_rules.json with the
independent oracle in tests/planning_reference.py. There is no product
calculation implementation yet, so nothing here is a product-calculation
passing claim; every assertion checks virtual fixture answers only.
Standard library only (unittest/itertools/Decimal/json).
"""

import json
import unittest
from decimal import Decimal
from itertools import combinations
from pathlib import Path

import tests.planning_reference as ref

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "planning_rules.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def weights_for(data, period):
    return {p["id"]: p["amounts_won"][period] for p in data["policies"]}


def independent_top3(eligible, weights, conflicts, limit=3):
    """Independent brute-force oracle: all subsets, keep maximal, rank top-k."""
    conflict_set = {tuple(sorted(p)) for p in conflicts}
    compat = []
    for r in range(len(eligible) + 1):
        for combo in combinations(sorted(eligible), r):
            combo_set = set(combo)
            bad = any(a in combo_set and b in combo_set for a, b in conflict_set)
            if not bad:
                compat.append(combo)
    maximal = []
    for combo in compat:
        combo_set = set(combo)
        if any(set(o) > combo_set and set(o) <= set(eligible) for o in compat):
            continue
        maximal.append(combo)
    ranked = sorted(
        maximal,
        key=lambda c: (-sum(weights[p] for p in c), len(c), c),
    )
    return [{"members": list(c), "total_won": sum(weights[p] for p in c)} for c in ranked[:limit]]


class FixtureShapeTests(unittest.TestCase):
    def test_amounts_are_won_integers(self):
        data = load_fixture()
        for policy in data["policies"]:
            for period, value in policy["amounts_won"].items():
                with self.subTest(policy=policy["id"], period=period):
                    self.assertIsInstance(value, int)
                    self.assertGreater(value, 0)
        by_id = {p["id"]: p for p in data["policies"]}
        self.assertEqual(by_id["P1"]["amounts_won"]["y1"], 2400000)
        self.assertEqual(by_id["P3"]["amounts_won"]["y1"], 3000000)
        self.assertEqual(by_id["P6"]["amounts_won"]["y2"], 3000000)

    def test_conflict_pairs_match_planning(self):
        data = load_fixture()
        pairs = {tuple(sorted(p)) for p in data["conflicts"]}
        self.assertEqual(pairs, {("P1", "P6"), ("P3", "P4")})


class CombinationAnswerTests(unittest.TestCase):
    def test_all_scenario_totals_match_independent_oracle(self):
        data = load_fixture()
        conflicts = [tuple(p) for p in data["conflicts"]]
        for scenario in data["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                weights = weights_for(data, scenario["period"])
                expected = scenario["expected"]
                ranked = ref.rank_portfolios(
                    scenario["eligible"], weights, conflicts, limit=3
                )
                self.assertEqual(len(ranked), len(expected))
                for got, want in zip(ranked, expected):
                    self.assertEqual(sorted(got["members"]), sorted(want["members"]))
                    self.assertEqual(got["total_won"], want["total_won"])
                for entry in ranked:
                    for closed in scenario.get("closed", []):
                        self.assertNotIn(closed, entry["members"])
                for entry in ranked:
                    self.assertTrue(ref.is_compatible(entry["members"], conflicts))

    def test_small_cases_agree_with_brute_force_oracle(self):
        data = load_fixture()
        conflicts = [tuple(p) for p in data["conflicts"]]
        for scenario in data["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                weights = weights_for(data, scenario["period"])
                limit = len(scenario["expected"])
                first = ref.rank_portfolios(
                    scenario["eligible"], weights, conflicts, limit=limit
                )
                second = independent_top3(
                    scenario["eligible"], weights, conflicts, limit=limit
                )
                self.assertEqual(first, second)

    def test_period_change_reorders_rank_one(self):
        data = load_fixture()
        by_id = {s["id"]: s for s in data["scenarios"]}
        self.assertEqual(by_id["A"]["expected"][0]["members"], ["P1", "P2", "P3"])
        self.assertEqual(by_id["A_2y"]["expected"][0]["members"], ["P2", "P3", "P6"])
        self.assertEqual(by_id["A"]["expected"][0]["total_won"], 6600000)
        self.assertEqual(by_id["A_2y"]["expected"][0]["total_won"], 8400000)

    def test_single_portfolio_and_fallback_shape(self):
        data = load_fixture()
        by_id = {s["id"]: s for s in data["scenarios"]}
        self.assertEqual(len(by_id["B"]["expected"]), 1)
        self.assertEqual(by_id["B"]["expected"][0]["total_won"], 2820000)
        self.assertEqual(len(by_id["D"]["expected"]), 2)
        self.assertEqual(len(by_id["E"]["expected"]), 2)

    def test_maturity_expected_amount_is_not_cash_timing(self):
        data = load_fixture()
        by_id = {p["id"]: p for p in data["policies"]}
        p2 = by_id["P2"]
        self.assertEqual(p2["amounts_won"]["y1"], 1200000)
        self.assertEqual(p2["pay_timing"], "at_maturity")
        accrual = p2["amounts_won"]["y1"]
        cash_y1 = 0
        self.assertNotEqual(accrual, cash_y1)
        self.assertEqual(
            ref.maturity_cashflow(p2["amounts_won"]["full"], 36),
            [(36, 3600000)],
        )


class TruncationRegressionTests(unittest.TestCase):
    def test_global_max_needs_universal_policy(self):
        data = load_fixture()
        reg = data["truncation_regression"]
        weights = {("T%02d" % i): reg["clique_amount_won"] for i in range(reg["clique_size"])}
        weights["U"] = reg["universal_amount_won"]
        conflicts = [
            (("T%02d" % i), ("T%02d" % j))
            for i in range(reg["clique_size"])
            for j in range(i + 1, reg["clique_size"])
        ]
        best, visited = ref.best_value_branch_and_bound(weights, conflicts)
        self.assertEqual(best, reg["expected_global_max_won"])
        self.assertLess(visited, 100000)
        self.assertLess(visited, 2 ** 21)
        top20 = sorted(weights, key=lambda n: -weights[n])[:20]
        self.assertNotIn("U", top20)
        self.assertEqual(max(weights[n] for n in top20), reg["naive_top20_max_won"])


class MilitaryExtensionTests(unittest.TestCase):
    def test_exact_months_boundary_with_policy_cap(self):
        self.assertEqual(ref.age_status(36, 34, service_months=20, extension_allowed=True, cap_years=6), "not_met")
        self.assertEqual(ref.age_status(36, 34, service_months=24, extension_allowed=True, cap_years=6), "met")
        rounded = 34 + -(-20 // 12)
        self.assertEqual(rounded, 36)
        self.assertEqual(ref.age_status(36, 34, service_months=20, extension_allowed=True, cap_years=6), "not_met")

    def test_range_all_pass_all_fail_and_straddle(self):
        self.assertEqual(ref.age_status(35, 34, service_range=(19, 24), extension_allowed=True, cap_years=6), "met")
        self.assertEqual(ref.age_status(38, 34, service_range=(19, 24), extension_allowed=True, cap_years=6), "not_met")
        data = load_fixture()
        reg = data["military_regression"]
        self.assertEqual(
            ref.age_status(36, 34, service_range=(reg["range_months_low"], reg["range_months_high"]), extension_allowed=True, cap_years=6),
            "needs_review",
        )

    def test_unknown_service_uses_bounds(self):
        self.assertEqual(ref.age_status(34, 34, service_unknown=True, extension_allowed=True, cap_years=6), "met")
        self.assertEqual(ref.age_status(41, 34, service_unknown=True, extension_allowed=True, cap_years=6), "not_met")
        self.assertEqual(ref.age_status(36, 34, service_unknown=True, extension_allowed=True, cap_years=6), "needs_review")
        self.assertEqual(ref.age_status(36, 34, service_months=None, extension_allowed=True, cap_years=6), "needs_review")

    def test_no_extension_basis_uses_base_limit(self):
        self.assertEqual(ref.age_status(36, 34, service_months=24, extension_allowed=False), "not_met")
        self.assertEqual(ref.age_status(34, 34, service_months=24, extension_allowed=False), "met")
        self.assertEqual(ref.age_status(36, 34, service_months=24), "not_met")

    def test_open_upper_range_with_known_cap(self):
        self.assertEqual(ref.age_status(36, 34, service_range=(24, None), extension_allowed=True, cap_years=6), "met")
        self.assertEqual(ref.age_status(41, 34, service_range=(24, None), extension_allowed=True, cap_years=6), "not_met")
        self.assertEqual(ref.age_status(38, 34, service_range=(24, None), extension_allowed=True, cap_years=6), "needs_review")
        self.assertEqual(ref.age_status(36, 34, service_range=(24, None), extension_allowed=True, cap_years=None), "needs_review")

    def test_per_policy_cap_is_enforced_and_required(self):
        self.assertEqual(ref.per_policy_cap(34, 96, 6), Decimal(40))
        self.assertEqual(ref.age_status(41, 34, service_months=96, extension_allowed=True, cap_years=6), "not_met")
        self.assertEqual(ref.age_status(40, 34, service_months=96, extension_allowed=True, cap_years=6), "met")
        with self.assertRaises(ValueError):
            ref.per_policy_cap(34, 24, None)
        self.assertEqual(ref.age_status(36, 34, service_months=24, extension_allowed=True, cap_years=None), "needs_review")


class RangeRuleTests(unittest.TestCase):
    def test_inverted_range_is_rejected(self):
        with self.assertRaises(ValueError):
            ref.check_closed_range(5000, 3000)
        with self.assertRaises(ValueError):
            ref.age_status(30, 34, service_range=(24, 19), extension_allowed=True, cap_years=6)
        with self.assertRaises(ValueError):
            ref.band_vs_threshold(50000000, 30000000, 30000000)

    def test_band_fully_under_is_met(self):
        self.assertEqual(ref.band_vs_threshold(0, 20000000, 30000000), "met")
        self.assertEqual(ref.band_vs_threshold(None, 20000000, 30000000), "met")

    def test_band_fully_over_is_not_met(self):
        self.assertEqual(ref.band_vs_threshold(50000000, None, 30000000), "not_met")
        self.assertEqual(ref.band_vs_threshold(31000000, 50000000, 30000000), "not_met")

    def test_threshold_equality_uses_the_policy_operator(self):
        self.assertEqual(ref.band_vs_threshold(15000000, 30000000, 30000000), "met")
        self.assertEqual(ref.band_vs_threshold(30000000, 30000000, 30000000), "met")
        self.assertEqual(ref.band_vs_threshold(30000000, 30000000, 30000000, inclusive=False), "not_met")
        self.assertEqual(ref.band_vs_threshold(15000000, 30000000, 30000000, inclusive=False), "needs_review")

    def test_band_crossing_or_unknown_is_needs_review(self):
        self.assertEqual(ref.band_vs_threshold(15000000, 31000000, 30000000), "needs_review")
        self.assertEqual(ref.band_vs_threshold(None, None, 30000000), "needs_review")
        self.assertEqual(ref.band_vs_threshold(None, 40000000, 30000000), "needs_review")
        self.assertEqual(ref.band_vs_threshold(15000000, None, 30000000), "needs_review")


class EffectiveRateTests(unittest.TestCase):
    def test_f_and_g_rates_use_annual_income_gap(self):
        data = load_fixture()
        for key, want in (("F", Decimal(45)), ("G", Decimal(12))):
            with self.subTest(case=key):
                case = data["income_change"][key]
                got = ref.effective_rate(
                    case["before_won"], case["after_won"], case["from_won"], case["to_won"]
                )
                self.assertEqual(got, want)

    def test_two_year_support_change_as_numerator_is_wrong(self):
        data = load_fixture()
        conflicts = [tuple(p) for p in data["conflicts"]]
        weights_y1 = weights_for(data, "y1")
        weights_y2 = weights_for(data, "y2")
        before_y1 = ref.rank_portfolios(["P1", "P2", "P4", "P6"], weights_y1, conflicts, limit=1)[0]["total_won"]
        after_y1 = ref.rank_portfolios(["P2", "P4", "P6"], weights_y1, conflicts, limit=1)[0]["total_won"]
        before_y2 = ref.rank_portfolios(["P1", "P2", "P4", "P6"], weights_y2, conflicts, limit=1)[0]["total_won"]
        after_y2 = ref.rank_portfolios(["P2", "P4", "P6"], weights_y2, conflicts, limit=1)[0]["total_won"]
        self.assertEqual(before_y1 - after_y1, 900000)
        income_gap = 31000000 - 29000000
        correct = ref.effective_rate(before_y1, after_y1, 29000000, 31000000)
        wrong = ref.effective_rate(before_y2, after_y2, 29000000, 31000000)
        self.assertEqual(correct, Decimal(45))
        self.assertNotEqual(wrong, Decimal(45))

    def test_flat_or_falling_income_has_no_rate(self):
        self.assertIsNone(ref.effective_rate(6600000, 6600000, 24000000, 24000000))
        self.assertIsNone(ref.effective_rate(6600000, 3720000, None, 24000000))
        self.assertIsNone(ref.effective_rate(2820000, 3720000, 31000000, 29000000))
        self.assertIsNone(ref.effective_rate(3720000, 3720000, 29000000, 29000000))


class PresentValueTests(unittest.TestCase):
    def test_monthly_payments_discount_each_month(self):
        flows = ref.monthly_cashflows(200000, 12)
        pv = ref.present_value(flows, Decimal("0.025"))
        self.assertLess(pv, Decimal(2400000))
        self.assertGreater(pv, Decimal(2000000))
        check = sum(
            Decimal(200000) / ((Decimal(1) + Decimal("0.025")) ** (Decimal(m) / Decimal(12)))
            for m in range(1, 13)
        )
        self.assertLess(abs(pv - check), Decimal("0.01"))

    def test_maturity_discounts_at_maturity_month(self):
        pv = ref.present_value(ref.maturity_cashflow(3600000, 36), Decimal("0.025"))
        exact = Decimal(3600000) / ((Decimal("1.025")) ** 3)
        self.assertLess(abs(pv - exact), Decimal("0.01"))
        self.assertLess(pv, Decimal(3600000))

    def test_unknown_rate_is_not_zero(self):
        self.assertIsNone(ref.present_value([(12, 1200000)], None))
        self.assertIsNotNone(ref.present_value([(12, 1200000)], Decimal("0.025")))


MVP_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mvp_planning_rules.json"


def load_mvp():
    return json.loads(MVP_FIXTURE.read_text(encoding="utf-8"))


class MvpFixtureShapeTests(unittest.TestCase):
    """First-MVP fixture shape: employment plus housing only (P1, P3, P6)."""

    def test_mvp_categories_within_youth_cover_employment_and_housing_only(self):
        data = load_mvp()
        by_id = {p["id"]: p for p in data["policies"]}
        self.assertEqual(sorted(by_id), ["P1", "P3", "P6"])
        self.assertEqual(by_id["P1"]["category"], "housing")
        self.assertEqual(by_id["P3"]["category"], "employment")
        self.assertEqual(by_id["P6"]["category"], "housing")
        self.assertEqual({p["category"] for p in data["policies"]}, {"housing", "employment"})
        self.assertEqual({p["domain"] for p in data["policies"]}, {"youth"})
        self.assertEqual(by_id["P1"]["amounts_won"], {"y1": 2400000, "y2": 2400000, "full": 2400000})
        self.assertEqual(by_id["P3"]["amounts_won"], {"y1": 3000000, "y2": 3000000, "full": 3000000})
        self.assertEqual(by_id["P6"]["amounts_won"], {"y1": 1500000, "y2": 3000000, "full": 3000000})
        for policy in data["policies"]:
            for value in policy["amounts_won"].values():
                with self.subTest(policy=policy["id"]):
                    self.assertIsInstance(value, int)
                    self.assertGreater(value, 0)
        pairs = {tuple(sorted(pair)) for pair in data["conflicts"]}
        self.assertEqual(pairs, {("P1", "P6")})
        self.assertIn("8.3", data["meta"]["source"])
        self.assertIn("v2.2", data["meta"]["source"])


class MvpCombinationTests(unittest.TestCase):
    """MVP answers with the same independent oracles as the 5-policy fixture."""

    def test_mvp_all_cases_match_independent_oracles(self):
        data = load_mvp()
        conflicts = [tuple(pair) for pair in data["conflicts"]]
        self.assertEqual(len(data["scenarios"]), 10)
        for scenario in data["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                weights = {p["id"]: p["amounts_won"][scenario["period"]] for p in data["policies"]}
                expected = scenario["expected"]
                ranked = ref.rank_portfolios(scenario["eligible"], weights, conflicts, limit=len(expected))
                self.assertEqual(len(ranked), len(expected))
                for got, want in zip(ranked, expected):
                    self.assertEqual(sorted(got["members"]), sorted(want["members"]))
                    self.assertEqual(got["total_won"], want["total_won"])
                brute = independent_top3(scenario["eligible"], weights, conflicts, limit=len(expected))
                self.assertEqual(ranked, brute)
                for entry in ranked:
                    self.assertTrue(ref.is_compatible(entry["members"], conflicts))
                    for closed in scenario.get("closed", []):
                        self.assertNotIn(closed, entry["members"])
                if scenario["id"] == "E":
                    self.assertEqual(scenario["closed"], ["P1"])
                    self.assertEqual(len(ranked), 1)
                    self.assertEqual(sorted(ranked[0]["members"]), ["P3", "P6"])
                    self.assertEqual(ranked[0]["total_won"], 4500000)

    def test_mvp_period_change_flips_rank_one(self):
        data = load_mvp()
        by_id = {s["id"]: s for s in data["scenarios"]}
        self.assertEqual(by_id["A"]["expected"][0]["members"], ["P1", "P3"])
        self.assertEqual(by_id["A"]["expected"][0]["total_won"], 5400000)
        self.assertEqual(by_id["A"]["expected"][1]["members"], ["P3", "P6"])
        self.assertEqual(by_id["A"]["expected"][1]["total_won"], 4500000)
        self.assertEqual(by_id["A_2y"]["expected"][0]["members"], ["P3", "P6"])
        self.assertEqual(by_id["A_2y"]["expected"][0]["total_won"], 6000000)
        self.assertEqual(by_id["A_2y"]["expected"][1]["members"], ["P1", "P3"])
        self.assertEqual(by_id["A_2y"]["expected"][1]["total_won"], 5400000)
        self.assertEqual(by_id["C"]["expected"], by_id["A"]["expected"])

    def test_mvp_single_policy_helper_result_is_not_api_list_claim(self):
        data = load_mvp()
        conflicts = [tuple(pair) for pair in data["conflicts"]]
        by_id = {s["id"]: s for s in data["scenarios"]}
        for sid in ("B", "D", "F_after"):
            with self.subTest(scenario=sid):
                scenario = by_id[sid]
                weights = {p["id"]: p["amounts_won"][scenario["period"]] for p in data["policies"]}
                ranked = ref.rank_portfolios(scenario["eligible"], weights, conflicts, limit=3)
                self.assertEqual(len(ranked), 1)
                self.assertEqual(len(scenario["expected"]), 1)
                self.assertEqual(sorted(ranked[0]["members"]), sorted(scenario["expected"][0]["members"]))
                self.assertEqual(ranked[0]["total_won"], scenario["expected"][0]["total_won"])
        # Calculation helper returning one combination describes the helper
        # output shape only. API list-card display and product UI behavior
        # are separate contracts and are not verified here.


class MvpIncomeChangeTests(unittest.TestCase):
    """MVP annual F/G cliff checks on y1 totals only."""

    def test_mvp_income_cliff_f_and_g_use_annual_gap(self):
        data = load_mvp()
        f_case = data["income_change"]["F"]
        g_case = data["income_change"]["G"]
        self.assertEqual((f_case["before_won"], f_case["after_won"]), (2400000, 1500000))
        self.assertEqual(f_case["lost_won"], 900000)
        self.assertEqual(f_case["net_gain_won"], 1100000)
        self.assertEqual(ref.effective_rate(f_case["before_won"], f_case["after_won"], f_case["from_won"], f_case["to_won"]), Decimal(45))
        self.assertEqual((g_case["before_won"], g_case["after_won"]), (5400000, 2400000))
        self.assertEqual(g_case["lost_won"], 3000000)
        self.assertEqual(ref.effective_rate(g_case["before_won"], g_case["after_won"], g_case["from_won"], g_case["to_won"]), Decimal("12.5"))
        conflicts = [tuple(pair) for pair in data["conflicts"]]
        weights_y1 = {p["id"]: p["amounts_won"]["y1"] for p in data["policies"]}
        f_before_best = ref.rank_portfolios(["P1", "P6"], weights_y1, conflicts, limit=1)[0]["total_won"]
        f_after_best = ref.rank_portfolios(["P6"], weights_y1, conflicts, limit=1)[0]["total_won"]
        self.assertEqual((f_before_best, f_after_best), (2400000, 1500000))
        g_before_best = ref.rank_portfolios(["P1", "P3", "P6"], weights_y1, conflicts, limit=1)[0]["total_won"]
        g_after_best = ref.rank_portfolios(["P1", "P6"], weights_y1, conflicts, limit=1)[0]["total_won"]
        self.assertEqual((g_before_best, g_after_best), (5400000, 2400000))


if __name__ == "__main__":
    unittest.main()
