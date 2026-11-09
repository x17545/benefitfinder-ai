"""Write a new local chunk extraction run; completion is written last."""

import json
from pathlib import Path

from benefitfinder.application.policy_rules import RuleReviewSnapshot
from benefitfinder.infrastructure.policy_rule_files import write_rule_snapshot


def _write(path: Path, data: dict[str, object]) -> None:
    with path.open('x', encoding='utf-8') as file:
        file.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def write_chunked_run(
    directory: Path, snapshot: RuleReviewSnapshot,
    facts: dict[str, object], provenance: dict[str, object],
) -> None:
    """Use an already reserved empty directory; incomplete runs have no completion marker."""
    _write(directory / 'facts-pending.json', facts)
    _write(directory / 'chunked-evidence.json', provenance)
    write_rule_snapshot(directory / 'rules-pending.json', snapshot)
    _write(directory / 'completion.json', {
        'format_version': 1, 'state': 'complete', 'status': 'needs_review',
        'approval_effect': 'none',
    })


def write_chunk_checkpoint(directory: Path, record: dict[str, object]) -> None:
    """Preserve a validated chunk immediately; this is not a completed extraction."""
    chunks = directory / "chunks"
    chunks.mkdir(exist_ok=True)
    _write(chunks / f"{record['index']:04d}.json", record)
