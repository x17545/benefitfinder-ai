"""Resumable offline batches; injected inference, compiler and publication only."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from benefitfinder.application.decider import Compilation, Decisions, canonical_hash
from benefitfinder.application.policy_analysis import PolicyAnalysisInput


def run_batch(
    values: tuple[PolicyAnalysisInput, ...], client: Decisions, *, root: Path,
    compile_policy: Callable[[PolicyAnalysisInput, tuple], Compilation],
    analyze: Callable[..., tuple[dict, dict]],
    save: Callable[[Path, object], None],
    publish: Callable[[dict, PolicyAnalysisInput], str] | None = None,
    batch_size: int = 4, window_chars: int = 1800,
) -> dict:
    if not 1 <= batch_size <= 32 or len({v.policy_id for v in values}) != len(values):
        raise ValueError("decider_invalid_policy_batch")
    entries = []
    started = monotonic()
    for index in range(0, len(values), batch_size):
        group = values[index:index + batch_size]
        outputs, failures = analyze(group, client, batch_size=batch_size,
                                    window_chars=window_chars)
        for value in group:
            identity = {"input_sha256": value.fingerprint(), "model": client.identity}
            key = canonical_hash(identity)
            directory = root / "policies" / key
            # Full input/model/questions/source identity is retained privately.
            save(directory / "input.json", {"format_version": 1, **value.payload()})
            status = {**identity, "policy_id": value.policy_id, "status": "running", "reasons": []}
            save(directory / "status.json", status)
            if value.policy_id in failures:
                status.update(status="failed", reasons=[failures[value.policy_id]])
            else:
                extracted = outputs[value.policy_id]
                save(directory / "extraction.json", {
                    **identity, "documents": [{"document_id": item.document.id,
                        "source_sha256": item.document.content_sha256,
                        "spans": [asdict(span) for span in item.spans],
                        "decisions": item.decisions} for item in extracted],
                })
                try:
                    compilation = compile_policy(value, extracted)
                    save(directory / "normalized.json", compilation.normalized)
                    if compilation.payload is None or compilation.reasons:
                        status.update(status="held", reasons=list(compilation.reasons)
                                      or ["decider_compilation_incomplete"])
                    else:
                        save(directory / "catalog.json", compilation.payload)
                        status["status"] = "dry_run_ready"
                        if publish is not None:
                            status["status"] = publish(compilation.payload, value)
                            if status["status"] not in {"published", "replayed", "replaced"}:
                                raise ValueError("decider_publication_status_invalid")
                except Exception as error:
                    # Only allowlisted fixed codes can leave the private adapter.
                    # Existing approvals/aliases need source preparation, never a
                    # bulk overwrite or another ID for the same real policy.
                    holds = {'ai_catalog_policy_alias_conflict',
                             'ai_catalog_existing_release_requires_separate_review',
                             'ai_catalog_version_hash_conflict', 'decider_source_changed'}
                    if isinstance(error, ValueError) and str(error) in holds:
                        status.update(status='held', reasons=[str(error)])
                    else:
                        status.update(status="failed", reasons=["decider_validation_or_publication_failed"])
            save(directory / "status.json", status)
            entries.append({**status, "artifact": str(directory)})
    counts = dict.fromkeys(("targets", "published", "replayed", "replaced", "dry_run_ready",
                           "held", "failed"), 0)
    counts["targets"] = len(values)
    counts.update(Counter(entry["status"] for entry in entries))
    report = {"format_version": 1, "model": client.identity, "counts": counts,
              "reason_counts": dict(Counter(reason for entry in entries for reason in entry["reasons"])),
              "finished_at": datetime.now(timezone.utc).isoformat(),
              "elapsed_seconds": round(monotonic() - started, 3), "entries": entries}
    save(root / "report.json", report)
    return report
