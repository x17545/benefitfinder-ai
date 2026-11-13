"""Reviewed recruitment windows across cash and non-cash recommendations."""

from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from backend.tests.catalog_fixtures import MemoryPolicies, release_data
from benefitfinder.api.schemas.recommendation import UserProfile
from benefitfinder.application.published_recommendations import PublishedRecommendations
from benefitfinder.application.recruitment import application_on
from benefitfinder.application.service_catalog import ServiceCatalog
from benefitfinder.infrastructure.example_services import example_services

TODAY = date(2026, 11, 13)
WINDOW = {
    "status": "upcoming", "starts_on": "2026-11-13", "ends_on": "2026-11-14",
    "url": "https://apply.example.test/fictional",
}


class RecruitmentDatesTests(unittest.TestCase):
    def test_start_and_inclusive_end_dates_keep_explicit_closure_and_unknown(self) -> None:
        cases = (
            ("upcoming", "2026-11-12", "upcoming"),
            ("upcoming", "2026-11-13", "open"),
            ("upcoming", "2026-11-14", "open"),
            ("upcoming", "2026-11-15", "closed"),
            ("open", "2026-11-12", "upcoming"),
            ("open", "2026-11-14", "open"),
            ("open", "2026-11-15", "closed"),
            ("closed", "2026-11-12", "closed"),
            ("closed", "2026-11-13", "closed"),
            ("unknown", "2026-11-13", "unknown"),
            ("unknown", "2026-11-15", "closed"),
        )
        for status, today, expected in cases:
            original = {**WINDOW, "status": status}
            with self.subTest(status=status, today=today):
                result = application_on(original, date.fromisoformat(today))
                self.assertEqual(result["status"], expected)
                self.assertEqual(original, {**WINDOW, "status": status})
                self.assertEqual(result["url"], WINDOW["url"])

    def test_missing_dates_do_not_invent_an_opening(self) -> None:
        for status in ("open", "upcoming", "closed", "unknown"):
            original = {**WINDOW, "status": status, "starts_on": None, "ends_on": None}
            with self.subTest(status=status):
                self.assertEqual(application_on(original, TODAY), original)
        self.assertEqual(
            application_on({**WINDOW, "ends_on": None}, TODAY)["status"], "open",
        )

    def test_cash_recommendations_enter_and_leave_on_reviewed_dates(self) -> None:
        data = release_data()
        data["benefit"]["application"] = WINDOW.copy()
        repository = MemoryPolicies(data)
        request = SimpleNamespace(
            domain="youth", profile=SimpleNamespace(domain_data=SimpleNamespace(interests=None)),
        )
        for today, expected in ((date(2026, 11, 12), "upcoming"),
                                (TODAY, "open"), (date(2026, 11, 14), "open"),
                                (date(2026, 11, 15), "closed")):
            with self.subTest(today=today):
                recommendations = PublishedRecommendations(
                    repository, lambda profile, rule: "met", today=lambda: today,
                )
                self.assertEqual(recommendations.detail(data["benefit"]["id"])
                                 ["application"]["status"], expected)
                calculation = recommendations(request)
                self.assertEqual(bool(calculation["catalog"]["payments"]), expected == "open")
                for period in calculation["results"].values():
                    self.assertEqual(bool(period["pv_off"]["amounts"]), expected == "open")
        self.assertEqual(repository.policies[0].detail["application"], WINDOW)

    def test_cash_snapshot_uses_one_day_for_all_policies(self) -> None:
        data = [release_data("A"), release_data("B")]
        for record in data:
            record["benefit"]["application"] = WINDOW.copy()
        clock = Mock(side_effect=[TODAY, date(2026, 11, 15)])
        recommendations = PublishedRecommendations(
            MemoryPolicies(*data), lambda profile, rule: "met", today=clock,
        )
        request = SimpleNamespace(
            domain="youth", profile=SimpleNamespace(domain_data=SimpleNamespace(interests=None)),
        )
        calculation = recommendations(request)
        clock.assert_called_once_with()
        self.assertEqual(len(calculation["catalog"]["payments"]), 2)

    def test_service_detail_and_recommendations_follow_the_same_window(self) -> None:
        payload = json.loads((Path(__file__).resolve().parents[2]
                              / "contracts/fixtures/recommendation-request.json").read_text())
        profile = UserProfile.model_validate(payload["profile"])
        offer = replace(example_services()[0], application=WINDOW.copy(), conditions=())
        for today, expected in ((date(2026, 11, 12), "upcoming"),
                                (TODAY, "open"), (date(2026, 11, 14), "open"),
                                (date(2026, 11, 15), "closed")):
            with self.subTest(today=today):
                catalog = ServiceCatalog((offer,), today=lambda: today)
                self.assertEqual(catalog.detail(offer.policy_id)["application"]["status"], expected)
                self.assertEqual(bool(catalog.recommend("youth", profile)["services"]),
                                 expected == "open")
        self.assertEqual(offer.application, WINDOW)
