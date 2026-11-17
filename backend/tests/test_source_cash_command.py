"""CLI composition tests use fictional sources and mock only external readers."""

from argparse import Namespace
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from backend.tests.test_source_cash import REGIONS, sample
from backend.tests.test_source_rent_cash import REGIONS as RENT_REGIONS, sample as rent_sample
from benefitfinder.jobs.__main__ import source_cash_command


class SourceCashCommandTest(unittest.TestCase):
    def run_command(self, value, regions, *, applicant_cash_facts=False):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            args = Namespace(output=output, as_of=date(2026, 11, 17), publish=False,
                             batch_size=10, applicant_cash_facts=applicant_cash_facts)
            connection = MagicMock()
            connection.__enter__.return_value.execute.return_value.fetchmany.return_value = []
            with (
                patch.dict("os.environ", {"POLICY_PUBLISH_DATABASE_URL": "postgresql://fixture/isolated"}),
                patch("psycopg.connect", return_value=connection),
                patch("benefitfinder.infrastructure.region_codes.OfficialRegionCatalog.get_regions",
                      return_value=regions),
                patch("benefitfinder.infrastructure.database.repositories.decider_inputs.load_decider_corpus",
                      return_value=((value,), {})),
                patch("benefitfinder.infrastructure.database.repositories.source_catalog_import.load_source_release_identities",
                      return_value=()),
                patch("benefitfinder.infrastructure.ai_catalog_review.publish_ai_batch") as publish,
                redirect_stdout(StringIO()),
            ):
                code = source_cash_command(args)
                publish.assert_not_called()
            return code, json.loads((output / "report.json").read_text())

    def test_rent_compiler_version_does_not_break_document_checkpoint_contract(self):
        code, report = self.run_command(rent_sample(), RENT_REGIONS)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["counts"]["dry_run_ready"], 1)
        self.assertEqual(report["counts"]["failed"], 0)
        self.assertEqual(report["source_version"], "source-facts-v1")
        self.assertTrue(report["dry_run"])

    def test_applicant_cash_requires_explicit_option_and_does_not_guess_calendar(self):
        value = sample(plcyNm="가상 응시료 지원", plcySprtCn="응시료 실비를 최대 10만원 지원")
        code, report = self.run_command(value, REGIONS)
        self.assertEqual(code, 1)
        self.assertEqual(report["counts"]["held"], 1)
        code, report = self.run_command(value, REGIONS, applicant_cash_facts=True)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["counts"]["dry_run_ready"], 1)
        self.assertEqual(report["counts"]["failed"], 0)
        self.assertEqual(report["source_version"], "source-facts-v1")
