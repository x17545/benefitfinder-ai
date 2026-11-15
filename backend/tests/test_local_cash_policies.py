"""Local cash data stays out of published portfolios and uses declared facts."""

from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from benefitfinder.application.analytics import AnalyticsService
from benefitfinder.bootstrap import create_app


SOURCE = {
    "url": "https://example.com/fictional-notice",
    "checked_at": "2026-01-01T00:00:00+09:00",
    "document_version": "fictional-v1",
}
EVIDENCE = {"quote": "가상 공고의 확인 조건", "location": "1절", "source": SOURCE}


def example_policy(kind="exam_reimbursement"):
    ids = ["age", "region", "중복 수급", "other_exclusions"]
    rule = {
        "kind": kind,
        "excluded_programs": ["fictional_support"],
        "application_ends_at": "2026-11-30T18:00:00+09:00",
    }
    if kind == "exam_reimbursement":
        ids += ["exam", "employment"]
        rule.update(
            exam_starts_on="2026-01-01",
            exam_ends_on="2026-11-30",
            total_cap_won=25_000,
            same_exam_limit=3,
            birth_year_min=1986,
            birth_year_max=2007,
        )
    else:
        ids += ["household", "income", "monthly_rent"]
        rule.update(
            covered_months=["2026-09", "2026-10"],
            monthly_cap_won=120_000,
            deposit_cap_won=50_000_000,
            contract_rent_cap_won=500_000,
            health_premium_caps={"employee": 100_000, "regional": 60_000},
            birth_date_min="1986-11-30",
            birth_date_max="2007-11-30",
        )
    return {
        "region_code": "41110",
        "rule": rule,
        "benefit": {
            "id": "youth:local:example",
            "domain": "youth",
            "title": "가상 실비",
            "summary": "자동 검사 전용",
            "provider": "가상 기관",
            "categories": ["employment" if kind == "exam_reimbursement" else "housing"],
            "application": {
                "status": "open",
                "starts_on": "2026-11-01",
                "ends_on": "2026-11-30",
                "url": None,
            },
            "source": SOURCE,
            "domain_data": {},
            "reviewed": False,
            "eligibility_conditions": [
                {"id": key, "description": "가상 조건", "evidence": [EVIDENCE]} for key in ids
            ],
        },
    }


class LocalCashPoliciesTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.json"
        self.at = datetime.fromisoformat("2026-11-16T12:00:00+09:00")

    def setup_client(self, policy):
        self.path.write_text(json.dumps({"policies": [policy]}))
        return TestClient(
            create_app(local_cash_policy_file=str(self.path), local_cash_reference_at=self.at)
        )

    def check(self, client, facts, **domain_data):
        response = client.post(
            "/api/v1/recommendations",
            json={
                "domain": "youth",
                "profile": {
                    "region_code": "41110",
                    "domain_data": {"cash_facts": facts, **domain_data},
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["virtual_data"])
        self.assertFalse(result["services"])
        basis = result["results"]["1y"]["pv_off"]
        self.assertFalse(basis["portfolios"])
        return basis

    def test_expense_is_capped_and_overlap_uses_exam_date(self):
        api = self.setup_client(example_policy())
        facts = {
            "birth_date": "2001-05-26",
            "program_periods": [],
            "exam": {
                "exam_date": "2026-11-02",
                "fee_won": 20_000,
                "supported_exam": True,
                "unemployed_on_exam_date": True,
                "same_exam_awards": 1,
                "prior_awards_won": 10_000,
                "other_reimbursement": False,
                "other_exclusions": False,
            },
        }
        entry = self.check(api, facts)["fallback_list"][0]
        self.assertEqual(entry["amount_in_period_won"], 15_000)
        self.assertIn("개별 추정액", entry["reason"])
        self.assertNotIn("amount_breakdown", entry)
        facts["program_periods"] = [
            {"program_id": "fictional_support", "starts_on": "2026-11-03", "ends_on": None}
        ]
        self.assertEqual(self.check(api, facts)["fallback_list"][0]["amount_in_period_won"], 15_000)
        facts["program_periods"][0]["starts_on"] = "2026-11-01"
        excluded = self.check(api, facts)
        self.assertEqual(excluded["not_eligible"][0]["failed"], ["중복 수급"])
        self.assertFalse(excluded["fallback_list"])
        facts.pop("program_periods")
        unknown = self.check(api, facts)
        self.assertEqual(unknown["not_yet_reviewed"][0]["eligibility_summary"], "needs_review")
        self.assertFalse(unknown["fallback_list"])

    def test_monthly_caps_and_paid_dates_with_current_overlap(self):
        api = self.setup_client(example_policy("rent_reimbursement"))
        housing = {
            "marital": "single",
            "household": {"household_size": 1},
            "housing": {"living": "alone", "home_ownership": "homeless", "household_head": "head"},
            "contract": {"type": "monthly_rent"},
        }
        facts = {
            "birth_date": "2001-05-26",
            "program_periods": [
                {
                    "program_id": "fictional_support",
                    "starts_on": "2026-09-01",
                    "ends_on": "2026-11-15",
                }
            ],
            "rent": {
                "deposit_won": 10_000_000,
                "contract_rent_won": 150_000,
                "health_insurance": "regional",
                "health_premium_won": 50_000,
                "applicant_lease": True,
                "other_exclusions": False,
                "paid_months": [
                    {"month": "2026-09", "paid_on": "2026-09-01", "amount_won": 150_000},
                    {"month": "2026-10", "paid_on": "2026-10-01", "amount_won": 90_000},
                    {"month": "2026-11", "paid_on": "2026-11-01", "amount_won": 150_000},
                ],
            },
        }
        nominal = self.check(api, facts, **housing)["fallback_list"]
        self.assertEqual(nominal[0]["amount_in_period_won"], 210_000)
        facts["rent"]["paid_months"][1]["paid_on"] = "2026-11-17"
        self.assertEqual(
            self.check(api, facts, **housing)["fallback_list"][0]["amount_in_period_won"], 120_000
        )
        facts["program_periods"][0]["ends_on"] = "2026-11-16"
        self.assertEqual(
            self.check(api, facts, **housing)["not_eligible"][0]["failed"], ["중복 수급"]
        )

    def test_local_catalog_rejects_approval_and_mixed_db_mode(self):
        policy = example_policy()
        with self.assertRaisesRegex(ValueError, "local_cash_requires_isolated_catalog"):
            create_app(local_cash_policy_file=str(self.path), policy_database_url="unused")
        policy["benefit"]["reviewed"] = True
        with self.assertRaisesRegex(ValueError, "reviewed must be false"):
            self.setup_client(policy)

    def test_local_catalog_disables_analytics(self):
        self.path.write_text(json.dumps({"policies": [example_policy()]}))
        service = AnalyticsService(enabled=True)
        with TestClient(create_app(analytics_service=service)) as client:
            self.assertEqual(client.get("/api/v1/analytics/config").json(), {"enabled": True})
        with TestClient(
            create_app(local_cash_policy_file=str(self.path), analytics_service=service)
        ) as client:
            self.assertEqual(client.get("/api/v1/analytics/config").json(), {"enabled": False})
