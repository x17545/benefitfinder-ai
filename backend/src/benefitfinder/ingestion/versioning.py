"""원천 레코드 해시와 직전 성공 버전 비교.

삭제 감지(``MISSING``)는 모든 페이지를 정상으로 받은 실행에서만 만든다. 일부 실패한
실행에서 사라진 것처럼 보이는 정책을 삭제로 기록하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from benefitfinder.application.collection import ChangeKind, CollectedRecord, RecordChange


def sha256_hex(content: bytes) -> str:
    """바이트 내용의 SHA-256 16진 문자열."""
    return hashlib.sha256(content).hexdigest()


def record_sha256(fields: Mapping[str, object]) -> str:
    """키 순서와 공백에 영향받지 않는 레코드 해시."""
    canonical = json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256_hex(canonical.encode("utf-8"))


def diff_records(
    source_name: str,
    previous: Mapping[str, str],
    current: Sequence[CollectedRecord],
    *,
    complete: bool,
) -> list[RecordChange]:
    """직전 해시와 이번 레코드를 비교해 변경 목록을 만든다."""
    changes: list[RecordChange] = []
    seen: set[str] = set()
    for record in current:
        seen.add(record.external_id)
        before = previous.get(record.external_id)
        if before is None:
            kind = ChangeKind.NEW
        elif before == record.record_sha256:
            kind = ChangeKind.UNCHANGED
        else:
            kind = ChangeKind.CHANGED
        changes.append(
            RecordChange(
                source_name=source_name,
                external_id=record.external_id,
                kind=kind,
                previous_sha256=before,
                current_sha256=record.record_sha256,
                snapshot_ref=record.snapshot_ref,
            )
        )
    if complete:
        for external_id in sorted(set(previous) - seen):
            changes.append(
                RecordChange(
                    source_name=source_name,
                    external_id=external_id,
                    kind=ChangeKind.MISSING,
                    previous_sha256=previous[external_id],
                    current_sha256=None,
                    snapshot_ref=None,
                )
            )
    return changes
