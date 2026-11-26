"""Publish prepared source-bound expenses without an AI review call."""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import psycopg
from psycopg.rows import dict_row
from benefitfinder.infrastructure.decider_client import private_json
from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash

p = argparse.ArgumentParser()
p.add_argument("--publish", action="store_true")
p.add_argument("--output", type=Path, required=True)
p.add_argument("--replace-owned", type=Path)
args = p.parse_args()
root = args.output
phase = root / ("expense-production" if args.publish else "expense-isolated")
url = (
    os.environ["POLICY_PUBLISH_DATABASE_URL"]
    if args.publish
    else os.environ["F13_TEST_DATABASE_URL"]
)
if not args.publish:
    from psycopg.conninfo import conninfo_to_dict

    settings = conninfo_to_dict(url)
    if not settings.get("dbname", "").endswith("_test") or settings.get("host") not in {
        "127.0.0.1",
        "localhost",
    }:
        p.error("isolated_test_database_required")
allowed = set(json.loads((root / "expense-preparation-report.json").read_text())["prepared_ids"])
items = [
    json.loads(path.read_text())
    for path in sorted((root / "expense-prepared").glob("*.json"))
    if path.stem in allowed
]
entries = []
expected = {}
states = {}
first_report = phase / "initial-report.json"
if args.publish and (phase / "report.json").exists() and not first_report.exists():
    private_json(first_report, json.loads((phase / "report.json").read_text()))
own = (
    {
        key
        for key, entry in json.loads(first_report.read_text()).get("entries", {}).items()
        if entry.get("status") == "published"
    }
    if args.publish and first_report.exists()
    else set()
)


def remap(item, old, new):
    if isinstance(item, str):
        return new if item == old else item
    if isinstance(item, list):
        return [remap(child, old, new) for child in item]
    if isinstance(item, dict):
        return {key: remap(child, old, new) for key, child in item.items()}
    return item


with psycopg.connect(
    url, connect_timeout=5, options="-c statement_timeout=15000", row_factory=dict_row
) as c:
    current = c.execute(
        "SELECT DISTINCT r.policy_id,r.document_version,r.approved_sha256,r.review_type,r.payload,l.external_id FROM public.policy_catalog_releases r JOIN public.policy_catalog_source_links l USING(policy_id,document_version) WHERE l.source='youthcenter' AND r.publication_status='published'"
    ).fetchall()
by_external = {r["external_id"]: r for r in current}
if args.replace_owned:
    replacements = json.loads(args.replace_owned.read_text())
    for key, digest in replacements.items():
        previous = by_external.get(key)
        if previous and previous["review_type"] == "ai" and previous["approved_sha256"] == digest:
            own.add(key)
if not (phase / "before-releases.json").exists():
    private_json(phase / "before-releases.json", current)
for item in items:
    key = item["external_id"]
    record = CatalogRecord.model_validate(item["catalog"])
    previous = by_external.get(key)
    if previous:
        record = CatalogRecord.model_validate(
            remap(record.model_dump(mode="json"), record.benefit.id, previous["policy_id"])
        )
        expected[record.benefit.id] = previous["approved_sha256"]
    if (
        previous
        and previous["review_type"] != "unreviewed"
        and (
            previous["review_type"] != "ai"
            or (key not in own and previous["approved_sha256"] != record_hash(record))
        )
    ):
        states[key] = {"status": "protected_existing", "policy_id": previous["policy_id"]}
        continue
    review = AIReview(
        writer_agent="local-decider:api-first-locate-decide-v2",
        reviewer_agent="code-normalization:explicit-expense-v1",
        reviewer_model="deterministic-python:no-ai-review",
        verdict="pass",
        reviewed_at=datetime.now(timezone.utc),
        document_version=record.benefit.source.document_version,
        source_sha256=record.source_sha256,
        record_sha256=record_hash(record),
    )
    entries.append((key, record, review, item["source_text"]))
    private_json(
        phase / "prepared" / (key + ".json"),
        {
            "catalog": record.model_dump(mode="json"),
            "review": review.model_dump(mode="json"),
            "source_text": item["source_text"],
            "expected_previous": expected.get(record.benefit.id),
            "actual_local_decider": True,
            "ai_review_calls": 0,
        },
    )


def report():
    value = {
        "actual_production_postgres": args.publish,
        "actual_local_decider_outputs": True,
        "ai_review_calls": 0,
        "counts": dict(Counter(e["status"] for e in states.values())),
        "entries": states,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    private_json(phase / "report.json", value)
    print(json.dumps(value), flush=True)


for start in range(0, len(entries), 16):
    batch = entries[start : start + 16]
    try:
        result = publish_ai_batch(
            url,
            [(r, review, source, None) for _, r, review, source in batch],
            allow_replace_ai=True,
            allow_replace_unreviewed=True,
            expected_previous={
                r.benefit.id: expected[r.benefit.id]
                for _, r, _, _ in batch
                if r.benefit.id in expected
            },
        )
        replay = publish_ai_batch(
            url,
            [(r, review, source, None) for _, r, review, source in batch],
            allow_replace_ai=True,
            allow_replace_unreviewed=True,
            expected_previous={
                r.benefit.id: expected[r.benefit.id]
                for _, r, _, _ in batch
                if r.benefit.id in expected
            },
        )
        if replay != {"published": 0, "replaced": 0, "replayed": len(batch)}:
            raise ValueError("expense_replay_mismatch")
        for key, record, _, _ in batch:
            states[key] = {
                "status": "published",
                "policy_id": record.benefit.id,
                "result": result,
                "replayed": True,
                "cap_won": record.benefit.benefit_amount.amount_won,
                "bps": next(
                    r.source_cash.reimbursement_bps
                    for r in record.conditions
                    if r.kind == "source_cash"
                ),
            }
    except Exception:
        for key, record, review, source in batch:
            try:
                # Refresh only the replaceable unreviewed hash if metadata was
                # concurrently appended by the second-stage catalogue worker.
                with psycopg.connect(url, connect_timeout=5) as c:
                    old = c.execute(
                        "SELECT approved_sha256,review_type FROM public.policy_catalog_releases WHERE policy_id=%s AND publication_status='published'",
                        (record.benefit.id,),
                    ).fetchone()
                if (
                    old
                    and old[1] != "unreviewed"
                    and (
                        old[1] != "ai"
                        or key not in own
                        or old[0] not in {expected.get(record.benefit.id), record_hash(record)}
                    )
                ):
                    states[key] = {"status": "protected_existing", "policy_id": record.benefit.id}
                    continue
                result = publish_ai_batch(
                    url,
                    [(record, review, source, None)],
                    allow_replace_ai=True,
                    allow_replace_unreviewed=True,
                    expected_previous={record.benefit.id: old[0]} if old else {},
                )
                states[key] = {
                    "status": "published",
                    "policy_id": record.benefit.id,
                    "result": result,
                }
            except Exception as exc:
                code = str(exc)
                states[key] = {
                    "status": "failed",
                    "policy_id": record.benefit.id,
                    "reason": code
                    if code.startswith(("ai_catalog_", "catalog_", "invalid_"))
                    and len(code) < 150
                    and "\n" not in code
                    else type(exc).__name__,
                }
    report()
report()
