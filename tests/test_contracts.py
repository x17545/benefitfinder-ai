"""Contract fixtures and cross-team invariants; no product API is implemented."""

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
FIXTURE_SCHEMAS = {
    "youth-benefit.json": "benefit.schema.json",
    "recommendation-request.json": "recommendation-request.schema.json",
    "recommendation-response.json": "recommendation-response.schema.json",
    "empty-response.json": "recommendation-response.schema.json",
    "error.json": "error.schema.json",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {
            path.name: read_json(path)
            for path in sorted((CONTRACTS / "v1").glob("*.schema.json"))
        }
        cls.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in cls.schemas.values()
        )

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name],
            registry=self.registry,
            format_checker=FormatChecker(),
        )

    def fixture(self, name):
        return read_json(CONTRACTS / "fixtures" / name)

    def test_schema_documents_and_local_references_are_valid(self):
        self.assertTrue(self.schemas, "No contract schemas found")
        self.assertEqual(
            len({schema["$id"] for schema in self.schemas.values()}),
            len(self.schemas),
        )
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                resolver = self.registry.resolver(base_uri=schema["$id"])
                for reference in references(schema):
                    resolver.lookup(reference)

    def test_every_fixture_matches_its_contract(self):
        fixtures = {path.name for path in (CONTRACTS / "fixtures").glob("*.json")}
        self.assertEqual(fixtures, set(FIXTURE_SCHEMAS))
        for fixture, schema in FIXTURE_SCHEMAS.items():
            with self.subTest(fixture=fixture):
                self.validator(schema).validate(self.fixture(fixture))

    def test_unknown_user_information_is_allowed(self):
        request = self.fixture("recommendation-request.json")
        for profile in ({}, {"age": None, "income_band": None}):
            with self.subTest(profile=profile):
                request["profile"] = profile
                self.validator("recommendation-request.schema.json").validate(request)

    def test_confirmed_conditions_require_evidence_but_review_can_be_pending(self):
        response = self.fixture("recommendation-response.json")
        result = response["recommendations"][0]["condition_results"][0]
        result["evidence"] = []
        validator = self.validator("recommendation-response.schema.json")
        for status in ("met", "not_met"):
            with self.subTest(status=status):
                result["status"] = status
                with self.assertRaises(ValidationError):
                    validator.validate(response)
        result["status"] = "needs_review"
        validator.validate(response)

    def test_source_version_and_valid_time_are_required(self):
        benefit = self.fixture("youth-benefit.json")
        invalid_sources = []
        missing_version = copy.deepcopy(benefit["source"])
        del missing_version["document_version"]
        invalid_sources.append(missing_version)
        for timestamp in ("yesterday", "2026-10-26T09:00:00", "2026-02-30T09:00:00Z"):
            invalid_sources.append({**benefit["source"], "checked_at": timestamp})
        for source in invalid_sources:
            with self.subTest(source=source):
                benefit["source"] = source
                with self.assertRaises(ValidationError):
                    self.validator("benefit.schema.json").validate(benefit)

    def test_another_domain_can_use_the_common_contract(self):
        # This checks extensible shape only, not support by a live API or its rules.
        request = self.fixture("recommendation-request.json")
        request["domain"] = "example_future_domain"
        request["profile"] = {"domain_data": {"example_field": "fictional"}}
        self.validator("recommendation-request.schema.json").validate(request)

        response = self.fixture("recommendation-response.json")
        response["domain"] = request["domain"]
        benefit = response["recommendations"][0]["benefit"]
        benefit["domain"] = request["domain"]
        benefit["id"] = "example_future_domain:example:001"
        benefit["domain_data"] = {"example_field": "fictional"}
        self.validator("recommendation-response.schema.json").validate(response)

    def test_invalid_profile_types_and_unexpected_fields_are_rejected(self):
        request = self.fixture("recommendation-request.json")
        for age in (-1, "25", True):
            with self.subTest(age=age):
                request["profile"]["age"] = age
                with self.assertRaises(ValidationError):
                    self.validator("recommendation-request.schema.json").validate(request)
        request = self.fixture("recommendation-request.json")
        request["unexpected_field"] = "not part of the contract"
        with self.assertRaises(ValidationError):
            self.validator("recommendation-request.schema.json").validate(request)


if __name__ == "__main__":
    unittest.main()
