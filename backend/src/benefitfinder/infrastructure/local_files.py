"""공용 DB가 정해지기 전까지 쓰는 로컬 파일 저장소.

- 원본 스냅숏: ``<raw_root>/<출처>/snapshots/`` 에 응답 본문과 메타데이터(JSON)
- 최신 레코드: ``<processed_root>/<출처>/records.json`` (정책 ID별 최신 버전)
- 변경 이력: ``<processed_root>/<출처>/changes.jsonl`` (신규·변경·삭제만)

경로는 ``data/raw``·``data/processed`` 같은 Git 제외 경로를 넘겨 사용한다. 최신 레코드와
변경 이력은 복구 기록을 먼저 남기고 저장하며, 중단된 저장은 다음 읽기 전에 완료한다.
같은 경로에는 한 번에 한 수집 프로세스만 실행한다.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from benefitfinder.application.collection import (
    ChangeKind,
    CollectedRecord,
    RawSnapshot,
    RecordChange,
)

RECORDS_FILE = "records.json"
CHANGES_FILE = "changes.jsonl"
PENDING_FILE = "pending.json"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class JsonFileSnapshotStore:
    """원본 응답을 그대로 파일로 남기고, 상대 경로를 참조값으로 돌려준다."""

    def __init__(self, raw_root: Path) -> None:
        self._root = raw_root

    def save(self, snapshot: RawSnapshot) -> str:
        stamp = snapshot.fetched_at.strftime("%Y%m%dT%H%M%S%z")
        page = snapshot.request.params.get("pageNum") or snapshot.request.params.get("page")
        page_part = f"-p{page}" if page else ""
        stem = f"{stamp}{page_part}-{snapshot.content_sha256[:12]}"
        is_json = (snapshot.content_type or "").split(";")[0].strip().endswith("json")
        suffix = "json" if is_json else "body"
        relative = Path(snapshot.source_name) / "snapshots" / f"{stem}.{suffix}"
        _write_atomic(self._root / relative, snapshot.body)
        meta = {
            "source_name": snapshot.source_name,
            "request_url": snapshot.request.url,
            "request_params": dict(snapshot.request.params),
            "fetched_at": snapshot.fetched_at.isoformat(),
            "status_code": snapshot.status_code,
            "content_type": snapshot.content_type,
            "content_sha256": snapshot.content_sha256,
            "body_file": relative.name,
        }
        _write_atomic(self._root / relative.with_name(f"{stem}.meta.json"), _json_bytes(meta))
        return relative.as_posix()


class JsonFileVersionStore:
    """정책 ID별 최신 레코드와 변경 이력을 JSON 파일로 관리한다."""

    def __init__(self, processed_root: Path) -> None:
        self._root = processed_root

    def records_path(self, source_name: str) -> Path:
        return self._root / source_name / RECORDS_FILE

    def changes_path(self, source_name: str) -> Path:
        return self._root / source_name / CHANGES_FILE

    def load_records(self, source_name: str) -> dict[str, dict[str, object]]:
        self._recover_source(source_name)
        path = self.records_path(source_name)
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: 레코드 파일 형식이 올바르지 않습니다.")
        return data

    def latest_hashes(self, source_name: str) -> Mapping[str, str]:
        return {
            external_id: str(entry["record_sha256"])
            for external_id, entry in self.load_records(source_name).items()
            if entry.get("missing_detected_at") is None
        }

    def save_versions(
        self,
        records: Sequence[CollectedRecord],
        changes: Sequence[RecordChange],
        *,
        checked_at: datetime,
    ) -> None:
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("checked_at은 시간대가 있는 시각이어야 합니다.")
        sources = {record.source_name for record in records} | {
            change.source_name for change in changes
        }
        for source_name in sources:
            self._save_source(
                source_name,
                [record for record in records if record.source_name == source_name],
                [change for change in changes if change.source_name == source_name],
                checked_at=checked_at,
            )

    def _save_source(
        self,
        source_name: str,
        records: Sequence[CollectedRecord],
        changes: Sequence[RecordChange],
        *,
        checked_at: datetime,
    ) -> None:
        stored = self.load_records(source_name)
        for record in records:
            stored[record.external_id] = {
                "record_sha256": record.record_sha256,
                "snapshot_ref": record.snapshot_ref,
                "last_checked_at": record.fetched_at.isoformat(),
                "missing_detected_at": None,
                "fields": dict(record.fields),
            }
        for change in changes:
            if change.kind is ChangeKind.MISSING and change.external_id in stored:
                stored[change.external_id]["missing_detected_at"] = checked_at.isoformat()

        logged = [
            {
                "recorded_at": checked_at.isoformat(),
                "external_id": change.external_id,
                "kind": change.kind.value,
                "previous_sha256": change.previous_sha256,
                "current_sha256": change.current_sha256,
                "snapshot_ref": change.snapshot_ref,
            }
            for change in changes
            if change.kind is not ChangeKind.UNCHANGED
        ]
        history = self.changes_path(source_name)
        pending = {
            "records": stored,
            "changes_offset": history.stat().st_size if history.exists() else 0,
            "changes": logged,
        }
        _write_atomic(self._root / source_name / PENDING_FILE, _json_bytes(pending))
        self._recover_source(source_name)

    def _recover_source(self, source_name: str) -> None:
        """저장 중단 지점부터 이력과 레코드를 복구한 뒤에만 최신 버전을 노출한다."""
        journal = self._root / source_name / PENDING_FILE
        if not journal.exists():
            return
        pending = json.loads(journal.read_text(encoding="utf-8"))
        if pending["changes"]:
            history = self.changes_path(source_name)
            lines = "".join(
                json.dumps(change, ensure_ascii=False) + "\n" for change in pending["changes"]
            ).encode("utf-8")
            with history.open("r+b" if history.exists() else "w+b") as handle:
                offset = pending["changes_offset"]
                if handle.seek(0, os.SEEK_END) < offset:
                    raise ValueError("변경 이력이 복구 기록보다 짧습니다.")
                # 부분 기록이나 이미 기록한 이벤트도 같은 위치에 덮어써 중복을 막는다.
                handle.seek(offset)
                handle.write(lines)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
        _write_atomic(self.records_path(source_name), _json_bytes(pending["records"]))
        journal.unlink()


def _field_names(records: Mapping[str, Mapping[str, object]]) -> list[str]:
    names: dict[str, None] = {}
    for entry in records.values():
        fields = entry.get("fields")
        if isinstance(fields, Mapping):
            names.update(dict.fromkeys(fields))
    return list(names)


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_records_csv(records: Mapping[str, Mapping[str, object]], path: Path) -> None:
    """최신 레코드를 엑셀에서 바로 열리는 CSV(UTF-8 BOM)로 내보낸다."""
    names = _field_names(records)
    header = ["external_id", "last_checked_at", "missing_detected_at", *names]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for external_id, entry in sorted(records.items()):
            fields = entry.get("fields")
            fields = fields if isinstance(fields, Mapping) else {}
            writer.writerow(
                [
                    external_id,
                    _cell(entry.get("last_checked_at")),
                    _cell(entry.get("missing_detected_at")),
                    *(_cell(fields.get(name)) for name in names),
                ]
            )
    os.replace(temporary, path)


def write_field_summary_csv(
    records: Mapping[str, Mapping[str, object]], path: Path, example_length: int = 40
) -> None:
    """필드별 채워진 건수·서로 다른 값 수·예시 값을 정리해 구조 공유용 CSV로 쓴다."""
    names = _field_names(records)
    total = len(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field", "filled", "total", "distinct", "example"])
        for name in names:
            values = []
            for entry in records.values():
                fields = entry.get("fields")
                if isinstance(fields, Mapping):
                    value = _cell(fields.get(name))
                    if value.strip():
                        values.append(value)
            example = values[0][:example_length] if values else ""
            writer.writerow([name, len(values), total, len(set(values)), example])
    os.replace(temporary, path)
