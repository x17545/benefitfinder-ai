"""Fetch a pinned official checkpoint; never execute unverified downloaded code."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from benefitfinder.application.decider import (
    MANIFEST_SHA256, METADATA_FILES, MODEL_ID as REPO, MODEL_REVISION as REVISION,
)


def verify(path: Path, expected: dict) -> bool:
    if not path.is_file() or path.stat().st_size != expected["bytes"]:
        return False
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest() == expected["sha256"]


def download(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = f"https://huggingface.co/{REPO}/resolve/{REVISION}/"
    headers = {"Authorization": "Bearer " + os.environ["HF_TOKEN"]} if os.getenv("HF_TOKEN") else {}
    with urlopen(Request(base + "release-manifest.json", headers=headers), timeout=60) as response:
        manifest_bytes = response.read()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise ValueError("decider_manifest_hash_mismatch")
    manifest = json.loads(manifest_bytes)
    if manifest["repo_id"] != REPO:
        raise ValueError("decider_manifest_mismatch")

    def fetch(item: tuple[str, dict]) -> None:
        name, expected = item
        expected = METADATA_FILES.get(name, expected)
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("decider_manifest_path_invalid")
        path = root / name
        if verify(path, expected):
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = path.with_name(path.name + ".partial")
        if verify(partial, expected):
            os.replace(partial, path)
            return
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= expected["bytes"]:
            # A complete corrupt transfer cannot be resumed with a range request.
            offset = 0
        request_headers = {**headers, **({"Range": f"bytes={offset}-"} if offset else {})}
        with urlopen(Request(base + name, headers=request_headers), timeout=180) as response:
            mode = "ab" if offset and response.status == 206 else "wb"
            with partial.open(mode) as target:
                partial.chmod(0o600)
                while chunk := response.read(8 * 1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        if not verify(partial, expected):
            # Retain incomplete bytes for an explicit restart, never load them.
            raise ValueError("decider_checkpoint_hash_mismatch")
        os.replace(partial, path)
        print(json.dumps({"file": name, "verified": True}), flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, manifest["files"].items()))
    (root / "release-manifest.json").write_bytes(manifest_bytes)
    (root / "revision.txt").write_text(REVISION + "\n", encoding="utf-8")
    for name in ("release-manifest.json", "revision.txt"):
        (root / name).chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/local/decider/checkpoint"))
    args = parser.parse_args()
    try:
        download(args.output)
    except Exception:
        raise SystemExit("decider_snapshot_failed (partial files retained; rerun to resume)") from None
