"""F08 launchd plist generator synthetic tests (no secrets, no install)."""

import plistlib
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import f08_launchagent as gen

FAKE_UV = "/usr/local/bin/uv"


class LaunchAgentTests(TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.project = self.tmp / "runtime"
        self.env = self.tmp / "test.env"
        self.data = self.tmp / "data"
        self.output = self.tmp / "dev.benefitfinder.f08.plist"

    def _args(self) -> list[str]:
        return [
            "--env-file",
            str(self.env),
            "--data-dir",
            str(self.data),
            "--output",
            str(self.output),
            "--uv",
            FAKE_UV,
            "--project-dir",
            str(self.project),
        ]

    def test_exact_schedule_program_and_no_on_load(self) -> None:
        with patch.object(gen, "current_gmtoff", return_value=32400):
            self.assertEqual(gen.main(self._args()), 0)
        data = plistlib.loads(self.output.read_bytes())
        proj = str(self.project.resolve())
        env = str(self.env.resolve())
        dat = str(self.data.resolve())
        self.assertEqual(data["Label"], "dev.benefitfinder.f08")
        self.assertEqual(data["StartCalendarInterval"], {"Hour": 3, "Minute": 0})
        self.assertIs(data["RunAtLoad"], False)
        self.assertEqual(data["WorkingDirectory"], proj)
        self.assertEqual(data["StandardOutPath"], dat + "/logs/f08.stdout.log")
        self.assertEqual(data["StandardErrorPath"], dat + "/logs/f08.stderr.log")
        self.assertEqual(
            data["ProgramArguments"],
            [
                FAKE_UV,
                "run",
                "--locked",
                "--no-sync",
                "--project",
                proj,
                "--env-file",
                env,
                "python",
                "-m",
                "benefitfinder.jobs",
                "refresh-youthcenter",
                "--data-dir",
                dat,
                "--notify-discord",
            ],
        )

    def test_existing_file_preserved(self) -> None:
        self.output.write_bytes(b"keep")
        with patch.object(gen, "current_gmtoff", return_value=32400):
            self.assertNotEqual(gen.main(self._args()), 0)
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_timezone_rejection_with_fixed_code(self) -> None:
        err = StringIO()
        with patch.object(gen, "current_gmtoff", return_value=0):
            with redirect_stderr(err):
                code = gen.main(self._args())
        self.assertEqual(code, gen.TIMEZONE_EXIT_CODE)
        self.assertFalse(self.output.exists())
        self.assertIn("launchd", err.getvalue())

    def test_generation_does_not_require_or_read_credentials(self) -> None:
        self.assertFalse(self.env.exists())
        with patch.object(gen, "current_gmtoff", return_value=32400):
            self.assertEqual(gen.main(self._args()), 0)
        self.assertFalse(self.env.exists())
        self.assertTrue((self.data / "logs").is_dir())
