"""F08 daily refresh systemd --user unit generator (generate only).

Builds a fixed service/timer pair that references the project, environment file,
and data directory. The environment file is never read or copied. This module
does not install, enable, start, or contact a systemd manager. The timer carries
an explicit KST calendar so it does not depend on the host timezone.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

SERVICE_NAME = "dev.benefitfinder.f08.service"
TIMER_NAME = "dev.benefitfinder.f08.timer"
JOB_CHOICES = ("refresh-data", "refresh-youthcenter")
DEFAULT_JOB = "refresh-data"
RUN_CALENDAR = "*-*-* 03:00:00 Asia/Seoul"


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F08 systemd --user unit generate only")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--uv", default=None)
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--job", choices=JOB_CHOICES, default=DEFAULT_JOB)
    return parser.parse_args(argv)


def _validate_job(job: str) -> None:
    if job not in JOB_CHOICES:
        raise ValueError("unsupported F08 job")


def _reject_control_characters(value: str) -> None:
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("path contains control characters")


def _absolute_path(path: Path) -> Path:
    _reject_control_characters(str(path))
    return path.expanduser().resolve()


def _unit_path_value(path: Path) -> str:
    """Escape a path setting; systemd expands specifiers without unquoting paths."""
    value = str(path)
    _reject_control_characters(value)
    value = value.replace("%", "%%")
    # A terminal backslash would continue the unit-file line. systemd strips
    # trailing value whitespace, so one trailing space preserves the backslash.
    return value + " " if value.endswith("\\") else value


def _systemd_exec_argument(value: str) -> str:
    """Quote one ExecStart argument using systemd.syntax rules.

    Backslashes are doubled and double quotes are escaped. Percent signs are
    doubled for unit specifier expansion. ExecStart uses the ":" executable
    prefix so dollar signs remain literal instead of starting environment
    variable substitution.
    """
    _reject_control_characters(value)
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _uv_env_file_argument(env_file: Path) -> str:
    """Return uv's single --env-file argument before systemd escaping.

    uv treats whitespace and backslashes inside this argument as delimited
    path syntax, so those characters need a leading backslash. The caller then
    quotes the resulting value for systemd's separate ExecStart parser.
    """
    value = str(env_file)
    _reject_control_characters(value)
    return "".join("\\" + character if character == "\\" or character.isspace() else character
                   for character in value)


def build_service(
    uv: str,
    project_dir: Path,
    env_file: Path,
    data_dir: Path,
    job: str = DEFAULT_JOB,
) -> str:
    _validate_job(job)
    uv_path = Path(uv)
    if not uv_path.is_absolute():
        raise ValueError("uv must be an absolute path")
    uv_path = _absolute_path(uv_path)
    project_dir = _absolute_path(project_dir)
    env_file = _absolute_path(env_file)
    data_dir = _absolute_path(data_dir)

    out_log = data_dir / "logs" / "f08.stdout.log"
    err_log = data_dir / "logs" / "f08.stderr.log"
    env_argument = _uv_env_file_argument(env_file)
    arguments = [
        str(uv_path),
        "run",
        "--locked",
        "--no-sync",
        "--project",
        str(project_dir),
        "--env-file",
        env_argument,
        "python",
        "-m",
        "benefitfinder.jobs",
        job,
        "--data-dir",
        str(data_dir),
        "--notify-discord",
    ]
    exec_start = "ExecStart=:" + " ".join(
        _systemd_exec_argument(argument) for argument in arguments
    )
    return "\n".join(
        (
            "[Unit]",
            "Description=BenefitFinder F08 daily refresh",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={_unit_path_value(project_dir)}",
            exec_start,
            "TimeoutStartSec=30min",
            "UMask=0077",
            f"StandardOutput=append:{_unit_path_value(out_log)}",
            f"StandardError=append:{_unit_path_value(err_log)}",
            "",
        )
    )


def build_timer() -> str:
    return "\n".join(
        (
            "[Unit]",
            "Description=BenefitFinder F08 daily refresh timer",
            "",
            "[Timer]",
            f"Unit={SERVICE_NAME}",
            f"OnCalendar={RUN_CALENDAR}",
            "AccuracySec=1s",
            "Persistent=false",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        )
    )


def _write_exclusive(path: Path, contents: str) -> None:
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(contents)
    except FileExistsError:
        raise
    except OSError:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_created(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        project_dir = _absolute_path(args.project_dir or default_root())
        env_file = _absolute_path(args.env_file)
        data_dir = _absolute_path(args.data_dir)
        output_dir = _absolute_path(args.output_dir)
        _validate_job(args.job)
    except (OSError, RuntimeError, ValueError):
        print("f08_systemd_invalid_path", file=sys.stderr)
        return 2

    uv = args.uv or shutil.which("uv")
    if not uv or not Path(uv).is_absolute():
        print("uv not found; pass --uv absolute path.", file=sys.stderr)
        return 2
    try:
        uv_path = _absolute_path(Path(uv))
    except (OSError, RuntimeError, ValueError):
        print("f08_systemd_invalid_path", file=sys.stderr)
        return 2

    service_path = output_dir / SERVICE_NAME
    timer_path = output_dir / TIMER_NAME
    if _path_present(service_path) or _path_present(timer_path):
        print("keep existing files; no files written", file=sys.stderr)
        return 1

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    except OSError:
        print("f08_systemd_write_failed", file=sys.stderr)
        return 1

    service = build_service(
        str(uv_path),
        project_dir,
        env_file,
        data_dir,
        job=args.job,
    )
    timer = build_timer()
    first_created: Path | None = None
    try:
        _write_exclusive(service_path, service)
        first_created = service_path
        _write_exclusive(timer_path, timer)
    except FileExistsError:
        _remove_created(first_created)
        print("keep existing files; no files written", file=sys.stderr)
        return 1
    except OSError:
        _remove_created(first_created)
        print("f08_systemd_write_failed", file=sys.stderr)
        return 1

    print(str(service_path))
    print(str(timer_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
