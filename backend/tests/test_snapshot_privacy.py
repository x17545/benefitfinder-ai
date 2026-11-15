"""Private snapshot writes stay private before, during, and after the atomic replace."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from benefitfinder.application.collection import RawSnapshot, SourceRequest
from benefitfinder.infrastructure.local_files import JsonFileSnapshotStore


SOURCE_NAME = "fictional-private-source"
BODY = b'{"fictional":"private snapshot body"}\n'


class SnapshotPrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.root = Path(self.temporary.name) / "raw"

    def snapshot(self) -> RawSnapshot:
        return RawSnapshot(
            source_name=SOURCE_NAME,
            request=SourceRequest(
                "https://fictional.invalid/private-snapshot",
                {"pageNum": "1"},
            ),
            fetched_at=datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc),
            status_code=200,
            content_type="application/json; charset=utf-8",
            content_sha256=hashlib.sha256(BODY).hexdigest(),
            body=BODY,
        )

    def paths(self, reference: str) -> tuple[Path, Path]:
        body_path = self.root / reference
        return body_path, body_path.with_suffix(".meta.json")

    def assert_mode(self, path: Path, expected: int) -> None:
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, str(path))

    def temporary_files(self, directory: Path) -> list[Path]:
        return [path for path in directory.iterdir() if path.name.endswith(".tmp")]

    def test_private_snapshot_modes_are_checked_during_write_and_before_replace(self) -> None:
        snapshot = self.snapshot()
        fsync_modes: list[int] = []
        replace_modes: list[int] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def checked_fsync(file_descriptor: int) -> None:
            status = os.fstat(file_descriptor)
            if stat.S_ISREG(status.st_mode):
                fsync_modes.append(stat.S_IMODE(status.st_mode))
            real_fsync(file_descriptor)

        def checked_replace(
            source: str | os.PathLike[str], target: str | os.PathLike[str]
        ) -> None:
            source_path = Path(source)
            target_path = Path(target)
            replace_modes.append(stat.S_IMODE(source_path.stat().st_mode))
            self.assert_mode(self.root, 0o700)
            self.assert_mode(self.root / SOURCE_NAME, 0o700)
            self.assert_mode(target_path.parent, 0o700)
            self.assertTrue(source_path.name.startswith("."))
            self.assertTrue(source_path.name.endswith(".tmp"))
            real_replace(source, target)

        with (
            mock.patch(
                "benefitfinder.infrastructure.local_files.os.fsync",
                side_effect=checked_fsync,
            ),
            mock.patch(
                "benefitfinder.infrastructure.local_files.os.replace",
                side_effect=checked_replace,
            ),
        ):
            reference = JsonFileSnapshotStore(self.root, private=True).save(snapshot)

        body_path, meta_path = self.paths(reference)
        self.assertEqual(fsync_modes, [0o600, 0o600])
        self.assertEqual(replace_modes, [0o600, 0o600])
        self.assertEqual(body_path.read_bytes(), snapshot.body)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["content_sha256"], snapshot.content_sha256)
        for path in (self.root, self.root / SOURCE_NAME, body_path.parent):
            self.assert_mode(path, 0o700)
        for path in (body_path, meta_path):
            self.assert_mode(path, 0o600)
        self.assertEqual(self.temporary_files(body_path.parent), [])

    def test_private_failure_secures_existing_files_and_preserves_their_contents(self) -> None:
        snapshot = self.snapshot()
        reference = JsonFileSnapshotStore(self.root).save(snapshot)
        body_path, meta_path = self.paths(reference)
        old_body = b'{"fictional":"previous body"}\n'
        old_meta = b'{"fictional":"previous metadata"}\n'
        body_path.write_bytes(old_body)
        meta_path.write_bytes(old_meta)
        stale_temporary = body_path.parent / f".{body_path.name}.crashed.tmp"
        stale_temporary.write_bytes(b"fictional stale temporary")

        for directory in (self.root, self.root / SOURCE_NAME, body_path.parent):
            self.assert_mode(directory, 0o755)
        for path in (body_path, meta_path):
            self.assert_mode(path, 0o644)

        with mock.patch(
            "benefitfinder.infrastructure.local_files.os.replace",
            side_effect=OSError("fictional replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "fictional replace failure"):
                JsonFileSnapshotStore(self.root, private=True).save(snapshot)

        self.assertEqual(body_path.read_bytes(), old_body)
        self.assertEqual(meta_path.read_bytes(), old_meta)
        for directory in (self.root, self.root / SOURCE_NAME, body_path.parent):
            self.assert_mode(directory, 0o700)
        for path in (body_path, meta_path):
            self.assert_mode(path, 0o600)
        self.assertEqual(self.temporary_files(body_path.parent), [])

        retry_reference = JsonFileSnapshotStore(self.root, private=True).save(snapshot)
        self.assertEqual(retry_reference, reference)
        self.assertEqual(body_path.read_bytes(), snapshot.body)
        self.assertEqual(
            json.loads(meta_path.read_text(encoding="utf-8"))["content_sha256"],
            snapshot.content_sha256,
        )
        self.assertEqual(self.temporary_files(body_path.parent), [])

    def test_public_snapshot_keeps_permissive_mode_and_existing_contract(self) -> None:
        snapshot = self.snapshot()
        reference = JsonFileSnapshotStore(self.root).save(snapshot)
        body_path, meta_path = self.paths(reference)

        for directory in (self.root, self.root / SOURCE_NAME, body_path.parent):
            self.assert_mode(directory, 0o755)
        for path in (body_path, meta_path):
            self.assert_mode(path, 0o644)
        self.assertEqual(body_path.read_bytes(), snapshot.body)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["content_sha256"], snapshot.content_sha256)
        self.assertEqual(metadata["body_file"], body_path.name)


if __name__ == "__main__":
    unittest.main()
