"""Fictional database rows verify Decider corpus identity selection."""

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.database.repositories.decider_inputs import load_decider_corpus


EXTERNAL_ID = "20260101000000001"
MATERIAL = "a" * 64
ACCEPTED_AT = datetime(2026, 11, 1, tzinfo=timezone.utc)


class Result:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def fetchall(self) -> list[dict]:
        return self.rows

    def fetchmany(self, size: int) -> list[dict]:
        assert size == 5001
        return self.rows

    def fetchone(self) -> dict | None:
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(
        self, *, aliases: list[tuple[str, str]], previous: list[dict] | None = None,
        supplements: dict[str, str] | None = None,
    ) -> None:
        self.aliases = aliases
        self.previous = previous or []
        self.supplements = supplements or {}
        self.alias_statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement: str, parameters=None) -> Result:
        if "public.policy_catalog_source_links link" in statement:
            self.alias_statement = statement
            assert parameters == ([EXTERNAL_ID],)
            return Result([
                {"external_id": external_id, "policy_id": policy_id}
                for external_id, policy_id in self.aliases
            ])
        if "FROM public.collected_policies p" in statement:
            fields = {
                "plcyNo": EXTERNAL_ID,
                "plcyNm": "가상 청년 주거 정책",
                "lclsfNm": "주거",
            }
            return Result([{
                "external_id": EXTERNAL_ID,
                "fields": fields,
                "material_sha256": MATERIAL,
                "accepted_at": ACCEPTED_AT,
            }])
        if "FROM public.f12_analysis_tasks" in statement:
            return Result(self.previous)
        if "SELECT material_sha256 FROM public.collected_policies" in statement:
            source, external_id = parameters
            return Result([{"material_sha256": self.supplements[(source, external_id)]}])
        return Result()


class DeciderInputsTest(TestCase):
    def load(self, connection: Connection):
        with patch(
            "benefitfinder.infrastructure.database.repositories.decider_inputs.psycopg.connect",
            return_value=connection,
        ):
            return load_decider_corpus("postgresql://virtual")

    def test_reuses_registered_numeric_catalog_policy_id(self) -> None:
        legacy_id = "youth:" + EXTERNAL_ID
        connection = Connection(aliases=[(EXTERNAL_ID, legacy_id)])

        values, selection = self.load(connection)

        self.assertEqual(values[0].policy_id, legacy_id)
        self.assertEqual(values[0].documents[0].policy_id, legacy_id)
        self.assertEqual(values[0].policies[0].id, legacy_id)
        self.assertEqual(selection["counts"], {
            "targets": 1, "selected": 1, "held": 0, "out_of_scope": 0,
        })
        self.assertIn("JOIN public.policy_catalog_releases", connection.alias_statement)
        self.assertIn("release.publication_status IN ('published','retired')", connection.alias_statement)

    def test_both_catalog_aliases_are_held_with_an_explicit_reason(self) -> None:
        legacy_id = "youth:" + EXTERNAL_ID
        generated_id = "youth:youthcenter:" + EXTERNAL_ID
        values, selection = self.load(Connection(aliases=[
            (EXTERNAL_ID, legacy_id), (EXTERNAL_ID, generated_id),
        ]))

        self.assertEqual(values, ())
        self.assertEqual(selection["counts"]["held"], 1)
        self.assertEqual(selection["reason_counts"], {"catalog_policy_alias_conflict": 1})

    def test_missing_catalog_alias_keeps_the_generated_identity(self) -> None:
        values, _ = self.load(Connection(aliases=[]))

        self.assertEqual(values[0].policy_id, "youth:youthcenter:" + EXTERNAL_ID)
        self.assertEqual(values[0].documents[0].id, "youthcenter:" + EXTERNAL_ID)

    def test_existing_supplement_is_preserved_under_the_reused_id(self) -> None:
        legacy_id = "youth:" + EXTERNAL_ID
        generated_id = "youth:youthcenter:" + EXTERNAL_ID
        supplement_id = "supplement:virtual-1"
        old = PolicyAnalysisInput(
            generated_id,
            (
                SourceDocument(
                    "youthcenter:" + EXTERNAL_ID, generated_id, "https://example.test/primary",
                    "sha256:" + MATERIAL, ACCEPTED_AT, "[가상] 원문", official=True,
                ),
                SourceDocument(
                    supplement_id, generated_id, "https://example.test/supplement",
                    "fictional-supplement-v1", ACCEPTED_AT, "[가상] 보충 공고", official=True,
                ),
            ),
            (AnalysisPolicy(generated_id, "가상 청년 주거 정책", ("housing",)),),
            (
                AnalysisSource("youthcenter", EXTERNAL_ID, MATERIAL),
                AnalysisSource("official_documents", supplement_id, "b" * 64),
            ),
        )
        connection = Connection(
            aliases=[(EXTERNAL_ID, legacy_id)],
            previous=[{"policy_id": generated_id, "input_payload": old.payload()}],
            supplements={
                ("youthcenter", EXTERNAL_ID): MATERIAL,
                ("official_documents", supplement_id): "b" * 64,
            },
        )

        values, selection = self.load(connection)

        self.assertEqual(values[0].policy_id, legacy_id)
        self.assertEqual(values[0].documents[1].id, supplement_id)
        self.assertEqual(values[0].collection_sources, old.collection_sources)
        self.assertEqual(selection["counts"]["selected"], 1)
