"""CLI exit codes and output privacy on virtual F12 evaluation files."""

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.tests.test_f12_evaluation_files import LINE1, setup_valid_tree
from benefitfinder.jobs.__main__ import main


class EvaluationCliTests(TestCase):
    def test_findings_exit_codes_console_privacy_and_create_only_output(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _, _ = setup_valid_tree(root)
            output = root / "report.json"
            args = ["evaluate-f12", "--manifest", str(manifest), "--output", str(output)]
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(args), 0)
            self.assertNotIn(LINE1, stdout.getvalue() + stderr.getvalue())
            before = output.read_bytes()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(args), 2)
            self.assertEqual(output.read_bytes(), before)
            facts = root / "cand/facts-pending.json"
            data = json.loads(facts.read_text())
            data["benefit_amount"]["items"][0]["evidence"]["quote"] = "가상 인용 오염"
            facts.write_text(json.dumps(data), encoding="utf-8")
            inputs = {p: p.read_bytes() for p in root.rglob("*.json")}
            args[-1] = str(root / "findings.json")
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(args), 1)
            self.assertTrue(all(p.read_bytes() == raw for p, raw in inputs.items()))
            report = json.loads((root / "findings.json").read_text())
            self.assertEqual(report["summary"]["citation_errors"], 1)
            self.assertEqual(report["approval_effect"], "none")

    def test_invalid_manifest_never_echoes_input_or_writes_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "invalid.json"
            manifest.write_text('{"format_version":1,"format_version":1,"private-marker":true}')
            output = root / "report.json"
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(["evaluate-f12", "--manifest", str(manifest),
                                       "--output", str(output)]), 2)
            self.assertIn("invalid_f12_evaluation_input", stderr.getvalue())
            self.assertNotIn("private-marker", stdout.getvalue() + stderr.getvalue())
            self.assertFalse(output.exists())
