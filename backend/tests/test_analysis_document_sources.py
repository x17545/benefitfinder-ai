"""Optional per-document source bindings preserve the legacy F12 input format."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from benefitfinder.application.policy_analysis import (
    AnalysisDocumentSource, AnalysisPolicy, AnalysisSource, PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.policy_analysis_inputs import (
    analysis_input_from_payload, load_policy_analysis_input,
)

AT = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)
LEGACY_FINGERPRINT = "287be90fc3e23f3275ed7557be84945385c6ca00e79c077b6e61e10af5565bbd"


def analysis_input(
    *, document_sources: tuple[AnalysisDocumentSource, ...] = ()
) -> PolicyAnalysisInput:
    policy_id = "youth:fictional:A"
    return PolicyAnalysisInput(
        policy_id,
        (
            SourceDocument(
                "fictional-document", policy_id, "https://example.test/fictional",
                "fictional-v1", AT, "가상 정책: 만 19세 이상 신청할 수 있습니다.", True,
            ),
        ),
        (AnalysisPolicy(policy_id, "가상 청년 취업 지원", ("employment",)),),
        (AnalysisSource("youthcenter", "A", "a" * 64),),
        document_sources,
    )


class AnalysisDocumentSourcesTest(unittest.TestCase):
    def test_empty_bindings_keep_legacy_payload_and_exact_fingerprint(self) -> None:
        value = analysis_input()

        self.assertNotIn("document_sources", value.payload())
        self.assertEqual(value.fingerprint(), LEGACY_FINGERPRINT)

    def test_old_and_new_v1_files_roundtrip(self) -> None:
        legacy = analysis_input()
        linked = analysis_input(document_sources=(
            AnalysisDocumentSource("fictional-document", "youthcenter", "A"),
        ))
        self.assertEqual(
            linked.payload()["document_sources"],
            ({"document_id": "fictional-document", "source": "youthcenter", "external_id": "A"},),
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, value in (("legacy.json", legacy), ("linked.json", linked)):
                path = root / name
                path.write_text(json.dumps({"format_version": 1, **value.payload()}))
                loaded = load_policy_analysis_input(path)
                self.assertEqual(loaded, value)
                self.assertEqual(loaded.fingerprint(), value.fingerprint())

        self.assertNotEqual(linked.fingerprint(), legacy.fingerprint())

    def test_payload_parser_accepts_list_or_tuple_for_strict_bindings(self) -> None:
        row = {"document_id": "fictional-document", "source": "youthcenter", "external_id": "A"}
        for bindings in ([row], (row,)):
            with self.subTest(type=type(bindings).__name__):
                payload = {**analysis_input().payload(), "document_sources": bindings}
                self.assertEqual(
                    analysis_input_from_payload(payload),
                    analysis_input(document_sources=(
                        AnalysisDocumentSource("fictional-document", "youthcenter", "A"),
                    )),
                )

    def test_unknown_unbound_duplicate_or_malformed_bindings_are_rejected(self) -> None:
        valid = AnalysisDocumentSource("fictional-document", "youthcenter", "A")
        invalid = (
            AnalysisDocumentSource("unknown-document", "youthcenter", "A"),
            AnalysisDocumentSource("fictional-document", "gov24", "A"),
            AnalysisDocumentSource("fictional-document", "unsupported", "A"),
            AnalysisDocumentSource(" ", "youthcenter", "A"),
            AnalysisDocumentSource("a\0b", "youthcenter", "A"),
            AnalysisDocumentSource("fictional-document", "youthcenter", " "),
            AnalysisDocumentSource("fictional-document", "youthcenter", "A\0"),
            AnalysisDocumentSource(1, "youthcenter", "A"),
            AnalysisDocumentSource("fictional-document", None, "A"),
            AnalysisDocumentSource("fictional-document", "youthcenter", 1),
        )
        for binding in invalid:
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                analysis_input(document_sources=(binding,))

        with self.assertRaises(ValueError):
            analysis_input(document_sources=(valid, valid))
        with self.assertRaises(ValueError):
            replace(analysis_input(), document_sources=[valid])

    def test_payload_parser_rejects_null_unknown_keys_and_invalid_rows(self) -> None:
        valid = analysis_input().payload()
        row = {"document_id": "fictional-document", "source": "youthcenter", "external_id": "A"}
        invalid_rows = (
            {**row, "extra": "not accepted"},
            {key: value for key, value in row.items() if key != "external_id"},
            {**row, "document_id": None},
            {**row, "source": 1},
            {**row, "external_id": None},
            {"document_id": "fictional-document", "source": "gov24", "external_id": "A"},
            {"document_id": "unknown-document", "source": "youthcenter", "external_id": "A"},
        )
        for payload in (
            {**valid, "document_sources": None},
            {**valid, "document_sources": row},
            *({**valid, "document_sources": [item]} for item in invalid_rows),
            {**valid, "document_sources": [row, row]},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError, "^invalid_policy_analysis_input$"
            ):
                analysis_input_from_payload(payload)


if __name__ == "__main__":
    unittest.main()
