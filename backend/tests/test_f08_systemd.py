"""F08 systemd --user generator synthetic tests (no secrets, no install)."""

import io
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import f08_systemd as gen

FAKE_UV = "/usr/local/bin/uv"


class SystemdGeneratorTests(TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.project = self.tmp / "runtime"
        self.env_file = self.tmp / "test.env"
        self.data = self.tmp / "data"
        self.output_dir = self.tmp / "units"

    def _args(
        self,
        *,
        env_file: Path | None = None,
        data_dir: Path | None = None,
        output_dir: Path | None = None,
        uv: str = FAKE_UV,
        project_dir: Path | None = None,
        job: str | None = None,
    ) -> list[str]:
        args = [
            "--env-file",
            str(env_file or self.env_file),
            "--data-dir",
            str(data_dir or self.data),
            "--output-dir",
            str(output_dir or self.output_dir),
            "--uv",
            uv,
            "--project-dir",
            str(project_dir or self.project),
        ]
        if job is not None:
            args.extend(("--job", job))
        return args

    def _unit_text(self, name: str, output_dir: Path | None = None) -> str:
        return (output_dir or self.output_dir).joinpath(name).read_text(encoding="utf-8")

    @staticmethod
    def _exec_start(service: str) -> str:
        return next(line for line in service.splitlines() if line.startswith("ExecStart="))

    def test_exact_default_service_timer_and_kst_schedule(self) -> None:
        self.assertEqual(gen.main(self._args()), 0)
        service = self._unit_text(gen.SERVICE_NAME)
        timer = self._unit_text(gen.TIMER_NAME)
        project = str(self.project.resolve())
        env_file = str(self.env_file.resolve())
        data_dir = str(self.data.resolve())
        expected_exec = (
            'ExecStart=:"/usr/local/bin/uv" "run" "--locked" "--no-sync" '
            f'"--project" "{project}" "--env-file" "{env_file}" "python" "-m" '
            f'"benefitfinder.jobs" "refresh-data" "--data-dir" "{data_dir}" '
            '"--notify-discord"'
        )
        self.assertEqual(
            service,
            "\n".join(
                (
                    "[Unit]",
                    "Description=BenefitFinder F08 daily refresh",
                    "",
                    "[Service]",
                    "Type=oneshot",
                    f"WorkingDirectory={project}",
                    expected_exec,
                    "TimeoutStartSec=30min",
                    "UMask=0077",
                    f"StandardOutput=append:{data_dir}/logs/f08.stdout.log",
                    f"StandardError=append:{data_dir}/logs/f08.stderr.log",
                    "",
                )
            ),
        )
        self.assertEqual(
            timer,
            "\n".join(
                (
                    "[Unit]",
                    "Description=BenefitFinder F08 daily refresh timer",
                    "",
                    "[Timer]",
                    "Unit=dev.benefitfinder.f08.service",
                    "OnCalendar=*-*-* 03:00:00 Asia/Seoul",
                    "AccuracySec=1s",
                    "Persistent=false",
                    "",
                    "[Install]",
                    "WantedBy=timers.target",
                    "",
                )
            ),
        )
        self.assertNotIn("[Install]", service)
        self.assertNotIn("systemctl", service + timer)

    def test_explicit_youthcenter_job(self) -> None:
        self.assertEqual(gen.main(self._args(job="refresh-youthcenter")), 0)
        service = self._unit_text(gen.SERVICE_NAME)
        exec_start = self._exec_start(service)
        self.assertIn('"refresh-youthcenter"', exec_start)
        self.assertNotIn('"refresh-data"', exec_start)

    def test_special_paths_are_quoted_and_escaped_without_shell(self) -> None:
        root = self.tmp / "space % $ ' \" \\ root"
        project = root / "runtime"
        env_file = root / "env folder" / "f08 env"
        data_dir = root / "data folder"
        output_dir = root / "unit folder"
        uv = root / "uv binary"
        args = self._args(
            env_file=env_file,
            data_dir=data_dir,
            output_dir=output_dir,
            uv=str(uv),
            project_dir=project,
        )
        self.assertEqual(gen.main(args), 0)
        service = self._unit_text(gen.SERVICE_NAME, output_dir)
        exec_start = self._exec_start(service)
        resolved_project = project.resolve()
        resolved_env = env_file.resolve()
        resolved_data = data_dir.resolve()
        uv_env_value = gen._uv_env_file_argument(resolved_env)

        self.assertTrue(exec_start.startswith('ExecStart=:"'))
        self.assertIn("\\ ", uv_env_value)
        self.assertIn("\\\\", uv_env_value)
        self.assertIn(gen._systemd_exec_argument(uv_env_value), exec_start)
        self.assertIn(gen._systemd_exec_argument(str(resolved_project)), exec_start)
        self.assertIn(gen._systemd_exec_argument(str(resolved_data)), exec_start)
        self.assertIn("%%", service)
        self.assertIn("$", service)
        self.assertIn('\\"', service)
        self.assertIn(
            f"WorkingDirectory={gen._unit_path_value(resolved_project)}",
            service,
        )
        self.assertIn(
            f"StandardOutput=append:{gen._unit_path_value(resolved_data / 'logs' / 'f08.stdout.log')}",
            service,
        )
        self.assertNotIn("/bin/sh", service)
        self.assertNotIn("sh -c", service)

    def test_control_and_null_paths_are_rejected_without_echo(self) -> None:
        for option in ("--env-file", "--data-dir", "--output-dir", "--project-dir", "--uv"):
            for number, invalid in enumerate(
                (str(self.tmp / "bad\npath"), str(self.tmp / "bad\0path"))
            ):
                with self.subTest(option=option, number=number):
                    output_dir = self.tmp / f"rejected-{option[2:]}-{number}"
                    args = self._args(output_dir=output_dir)
                    args[args.index(option) + 1] = invalid
                    stderr = StringIO()
                    with redirect_stderr(stderr):
                        code = gen.main(args)
                    self.assertEqual(code, 2)
                    self.assertNotIn(invalid, stderr.getvalue())
                    self.assertFalse((output_dir / gen.SERVICE_NAME).exists())
                    self.assertFalse((output_dir / gen.TIMER_NAME).exists())

    def test_existing_pair_and_one_sided_pairs_are_preserved(self) -> None:
        cases = (("service", (gen.SERVICE_NAME,)), ("timer", (gen.TIMER_NAME,)),
                 ("both", (gen.SERVICE_NAME, gen.TIMER_NAME)))
        for label, names in cases:
            with self.subTest(label=label):
                output_dir = self.tmp / f"existing-{label}"
                output_dir.mkdir()
                markers = {name: f"keep-{name}".encode() for name in names}
                for name, marker in markers.items():
                    (output_dir / name).write_bytes(marker)
                with redirect_stderr(StringIO()):
                    code = gen.main(self._args(output_dir=output_dir))
                self.assertEqual(code, 1)
                for name in (gen.SERVICE_NAME, gen.TIMER_NAME):
                    path = output_dir / name
                    if name in markers:
                        self.assertEqual(path.read_bytes(), markers[name])
                    else:
                        self.assertFalse(path.exists())

    def test_second_write_failure_removes_only_first_created_file(self) -> None:
        real_write = gen._write_exclusive

        def fail_timer(path: Path, contents: str) -> None:
            if path.name == gen.TIMER_NAME:
                raise OSError("synthetic write failure")
            real_write(path, contents)

        stderr = StringIO()
        with patch.object(gen, "_write_exclusive", side_effect=fail_timer):
            with redirect_stderr(stderr):
                code = gen.main(self._args())
        self.assertEqual(code, 1)
        self.assertIn("f08_systemd_write_failed", stderr.getvalue())
        self.assertNotIn("synthetic", stderr.getvalue())
        self.assertFalse((self.output_dir / gen.SERVICE_NAME).exists())
        self.assertFalse((self.output_dir / gen.TIMER_NAME).exists())

    def test_second_file_race_preserves_external_file_and_removes_first(self) -> None:
        real_write = gen._write_exclusive

        def race_timer(path: Path, contents: str) -> None:
            if path.name == gen.TIMER_NAME:
                path.write_bytes(b"external")
                raise FileExistsError(path)
            real_write(path, contents)

        with patch.object(gen, "_write_exclusive", side_effect=race_timer):
            with redirect_stderr(StringIO()):
                code = gen.main(self._args())
        self.assertEqual(code, 1)
        self.assertFalse((self.output_dir / gen.SERVICE_NAME).exists())
        self.assertEqual((self.output_dir / gen.TIMER_NAME).read_bytes(), b"external")

    def test_generation_does_not_read_or_echo_env_contents(self) -> None:
        self.env_file.write_text(
            "F08_TEST_PLACEHOLDER=not-a-real-secret\n",
            encoding="utf-8",
        )
        protected = self.env_file.resolve()
        real_open = io.open

        def guarded_open(file, *args, **kwargs):
            if Path(file) == protected:
                raise AssertionError("env file was opened")
            return real_open(file, *args, **kwargs)

        stdout = StringIO()
        stderr = StringIO()
        with patch("io.open", side_effect=guarded_open):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = gen.main(self._args())
        self.assertEqual(code, 0)
        emitted = stdout.getvalue() + stderr.getvalue()
        emitted += self._unit_text(gen.SERVICE_NAME) + self._unit_text(gen.TIMER_NAME)
        self.assertNotIn("F08_TEST_PLACEHOLDER", emitted)
        self.assertNotIn("not-a-real-secret", emitted)
        self.assertEqual(
            self.env_file.read_text(encoding="utf-8"),
            "F08_TEST_PLACEHOLDER=not-a-real-secret\n",
        )

    def test_uv_env_file_argument_round_trips_spaces_and_backslash(self) -> None:
        uv = shutil.which("uv")
        if uv is None:
            self.skipTest("uv is required for this command-boundary check")
        env_file = self.tmp / "space % $ ' \" \\ env folder" / "sample.env"
        env_file.parent.mkdir()
        env_file.write_text("F08_VIRTUAL_FLAG=confirmed\n", encoding="utf-8")
        value = gen._uv_env_file_argument(env_file.resolve())
        result = subprocess.run(
            [
                uv,
                "run",
                "--no-project",
                "--python",
                sys.executable,
                "--env-file",
                value,
                "python",
                "-c",
                "import os; print(os.environ.get('F08_VIRTUAL_FLAG'))",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "confirmed"))

    def test_generation_does_not_install_enable_start_or_contact_manager(self) -> None:
        with (
            patch("subprocess.run") as run,
            patch("subprocess.Popen") as popen,
            patch("os.system") as system,
        ):
            with redirect_stdout(StringIO()):
                self.assertEqual(gen.main(self._args()), 0)
        run.assert_not_called()
        popen.assert_not_called()
        system.assert_not_called()

    def test_generated_files_verify_special_paths_and_explicit_kst(self) -> None:
        analyze = shutil.which("systemd-analyze")
        uv = shutil.which("uv")
        if analyze is None or uv is None:
            self.skipTest("systemd-analyze and uv are required for this check")
        root = self.tmp / "space % $ ' \" \\ verify root"
        project = root / "runtime\\"
        env_file = root / "env folder" / "f08 env"
        data_dir = root / "data folder"
        output_dir = root / "unit folder"
        project.mkdir(parents=True)
        self.assertEqual(
            gen.main(
                self._args(
                    env_file=env_file,
                    data_dir=data_dir,
                    output_dir=output_dir,
                    uv=uv,
                    project_dir=project,
                )
            ),
            0,
        )
        verify_env = {**os.environ, "TZ": "UTC", "SYSTEMD_LOG_LEVEL": "debug"}
        verify = subprocess.run(
            [
                analyze,
                "verify",
                "--man=no",
                str(output_dir / gen.SERVICE_NAME),
                str(output_dir / gen.TIMER_NAME),
            ],
            capture_output=True,
            text=True,
            env=verify_env,
        )
        verified_output = verify.stdout + "\n" + verify.stderr
        self.assertEqual(verify.returncode, 0, verified_output)
        resolved_project = project.resolve()
        resolved_data = data_dir.resolve()
        self.assertIn(f"WorkingDirectory: {resolved_project}", verified_output)
        self.assertIn(
            f"StandardOutputFileToAppend: {resolved_data / 'logs' / 'f08.stdout.log'}",
            verified_output,
        )
        self.assertIn(
            f"StandardErrorFileToAppend: {resolved_data / 'logs' / 'f08.stderr.log'}",
            verified_output,
        )

        calendar = subprocess.run(
            [analyze, "calendar", gen.RUN_CALENDAR],
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "UTC"},
        )
        self.assertEqual(calendar.returncode, 0, calendar.stderr)
        self.assertIn("Asia/Seoul", calendar.stdout + calendar.stderr)
