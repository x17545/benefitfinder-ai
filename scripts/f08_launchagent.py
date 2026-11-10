"""F08 youthcenter refresh plist generator (generate only).

Builds a plist holding a uv run command from a project directory.
Main prepares an immutable ignored runtime dir separately;
this file only references it, never copies or deploys.
Does not install, run, or read secrets; env file path only.
launchd calendar follows host timezone, so require KST host.
Installation is done separately after file review.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LABEL = "dev.benefitfinder.f08"
KST_GMTOFF = 32400
SYSTEM_LOCALTIME = Path("/etc/localtime")
RUN_HOUR = 3
RUN_MINUTE = 0
TIMEZONE_EXIT_CODE = 2


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def current_gmtoff(localtime: Path = SYSTEM_LOCALTIME) -> int:
    """Read the host timezone without consulting the caller's TZ environment."""
    with localtime.open("rb") as handle:
        system_zone = ZoneInfo.from_file(handle, key="system-localtime")
    offset = datetime.now(system_zone).utcoffset()
    if offset is None:
        raise ValueError("system timezone has no UTC offset")
    return int(offset.total_seconds())


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F08 launchd plist generate only")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", default=None)
    parser.add_argument("--project-dir", type=Path, default=None)
    return parser.parse_args(argv)


def build_plist(uv: str, project_dir: Path, env_file: Path, data_dir: Path) -> dict[str, object]:
    out_log = data_dir / "logs" / "f08.stdout.log"
    err_log = data_dir / "logs" / "f08.stderr.log"
    # uv interprets --env-file as a whitespace-delimited list, even in one argv entry.
    env_argument = "".join(
        "\\" + char if char == "\\" or char.isspace() else char for char in str(env_file)
    )
    program = [
        uv,
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
        "refresh-youthcenter",
        "--data-dir",
        str(data_dir),
        "--notify-discord",
    ]
    return {
        "Label": LABEL,
        "ProgramArguments": program,
        "WorkingDirectory": str(project_dir),
        "StartCalendarInterval": {"Hour": RUN_HOUR, "Minute": RUN_MINUTE},
        "RunAtLoad": False,
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = args.project_dir.resolve() if args.project_dir else default_root()
    env_file = Path(args.env_file).resolve()
    data_dir = Path(args.data_dir).resolve()
    output = Path(args.output)
    uv = args.uv or shutil.which("uv")
    if not uv or not Path(uv).is_absolute():
        print("uv not found; pass --uv absolute path.", file=sys.stderr)
        return 2
    try:
        offset = current_gmtoff()
    except (OSError, ValueError):
        print("system timezone unavailable; need KST host.", file=sys.stderr)
        return TIMEZONE_EXIT_CODE
    if offset != KST_GMTOFF:
        print(
            "launchd calendar uses host local timezone. "
            + f"current tm_gmtoff={offset}, need KST(32400). Regenerate on KST host.",
            file=sys.stderr,
        )
        return TIMEZONE_EXIT_CODE
    if output.exists():
        print(f"keep existing file: {output}", file=sys.stderr)
        return 1
    plist_data = build_plist(uv, project_dir, env_file, data_dir)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        Path(str(plist_data["StandardOutPath"])).parent.mkdir(parents=True, exist_ok=True)
        with open(output, "xb") as handle:
            plistlib.dump(plist_data, handle)
    except FileExistsError:
        print(f"keep existing file: {output}", file=sys.stderr)
        return 1
    except OSError:
        print("f08_plist_write_failed", file=sys.stderr)
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
