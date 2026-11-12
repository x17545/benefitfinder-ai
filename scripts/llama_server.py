"""Run an installed llama.cpp model on the local GPU; never download or publish it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--cuda-lib-dir", type=Path)
    parser.add_argument("--alias", default="gemma4-e4b")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--key-file", type=Path, default=ROOT / "data/local/llama-cpp/api-key")
    args = parser.parse_args()
    if not args.server.is_file() or not args.model.is_file():
        parser.error("설치된 llama-server와 GGUF 모델 경로를 지정해 주세요.")
    if not 1 <= args.port <= 65535 or not 512 <= args.context_size <= 65536:
        parser.error("포트 또는 문맥 길이가 올바르지 않습니다.")
    environment = os.environ.copy()
    if args.cuda_lib_dir is not None:
        if not args.cuda_lib_dir.is_dir():
            parser.error("CUDA 라이브러리 폴더를 확인해 주세요.")
        variable = "PATH" if os.name == "nt" else "LD_LIBRARY_PATH"
        environment[variable] = (
            str(args.cuda_lib_dir.resolve()) + os.pathsep + environment.get(variable, "")
        )
    try:
        check = subprocess.run(
            [str(args.server.resolve()), "--list-devices"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        parser.error("llama.cpp 장치를 조회하지 못했습니다.")
    if "CUDA" not in check.stdout + check.stderr:
        parser.error("CUDA GPU를 인식하지 못했습니다. CUDA 라이브러리 경로를 확인해 주세요.")
    args.key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not args.key_file.is_file() or not args.key_file.read_text().strip():
            parser.error("로컬 API 키 파일을 확인해 주세요.")
    else:
        with os.fdopen(descriptor, "w") as target:
            target.write(secrets.token_urlsafe(32) + "\n")
    command = [
        str(args.server.resolve()),
        "--model",
        str(args.model.resolve()),
        "--alias",
        args.alias,
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--ctx-size",
        str(args.context_size),
        "--parallel",
        "1",
        "--n-gpu-layers",
        "99",
        "--jinja",
        "--reasoning",
        "off",
        "--n-predict",
        "1024",
        "--api-key-file",
        str(args.key_file.resolve()),
    ]
    print(f"llama.cpp: localhost:{args.port}, CUDA, context={args.context_size}", flush=True)
    try:
        return subprocess.call(command, env=environment)
    except KeyboardInterrupt:
        return 130
    except OSError:
        print("llama.cpp 서버를 시작하지 못했습니다.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
