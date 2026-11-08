"""Failure boundaries for the two local F12 review files."""

import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from benefitfinder.application.policy_facts import PolicyFactsDraft
from benefitfinder.infrastructure.policy_rule_files import (
    RuleReviewFileError,
    write_f12_review_pair,
)
from benefitfinder.ingestion.rule_review_workflow import prepare_snapshot
from benefitfinder.jobs.rule_review_example import example_rule_batch


class F12ReviewPairTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "pending"
        batch = example_rule_batch()
        self.snapshot = prepare_snapshot(batch, at=datetime(2026, 11, 6, tzinfo=timezone.utc))
        self.draft = PolicyFactsDraft(batch.documents[0], (), (), ())

    def test_failed_second_write_does_not_expose_rules(self) -> None:
        with (
            patch(
                "benefitfinder.infrastructure.policy_rule_files.write_policy_facts",
                side_effect=RuleReviewFileError("invalid_rule_review_file"),
            ),
            self.assertRaisesRegex(RuleReviewFileError, "invalid_rule_review_file"),
        ):
            write_f12_review_pair(self.directory, self.snapshot, self.draft)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_second_link_removes_only_its_partial_result(self) -> None:
        link = os.link

        def fail_on_facts(source: Path, destination: Path) -> None:
            if Path(destination) == self.directory / "facts-pending.json":
                raise OSError("simulated link failure")
            link(source, destination)

        with (
            patch(
                "benefitfinder.infrastructure.policy_rule_files.os.link", side_effect=fail_on_facts
            ),
            self.assertRaisesRegex(RuleReviewFileError, "rule_review_io_error"),
        ):
            write_f12_review_pair(self.directory, self.snapshot, self.draft)
        self.assertEqual(list(self.root.iterdir()), [])
