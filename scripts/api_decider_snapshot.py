"""Idempotently load a previously collected official API generation.

This uses the retained API fields, never claims a new API fetch, and never
changes published calculation records.
"""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import asdict
import psycopg
from benefitfinder.application.collection import CollectedRecord
from benefitfinder.application.collected_policies import CollectedPolicyBatch
from benefitfinder.infrastructure.database.repositories.collected_policies import (
    PostgresCollectedPolicies,
)
from benefitfinder.infrastructure.decider_client import private_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.output
url = os.environ["POLICY_PUBLISH_DATABASE_URL"]
with psycopg.connect(url, connect_timeout=5, options="-c statement_timeout=15000") as connection:
    rows = connection.execute(
        "SELECT jsonb_build_object('source_name',source,'external_id',external_id,'record_sha256',material_sha256,'snapshot_ref',snapshot_ref,'fetched_at',fetched_at,'fields',fields) FROM public.collected_policies WHERE source='youthcenter' AND missing_detected_at IS NULL ORDER BY external_id"
    ).fetchall()
    latest = connection.execute(
        "SELECT run_id FROM public.collected_policy_runs WHERE source='youthcenter' ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()[0]
    before = dict(
        connection.execute(
            "SELECT policy_id,approved_sha256 FROM public.policy_catalog_releases WHERE publication_status='published'"
        ).fetchall()
    )
raw = [row[0] for row in rows]
private_json(root / "api-originals.json", raw)
private_json(root / "catalog-before.json", before)
records = tuple(
    CollectedRecord(
        row["source_name"],
        row["external_id"],
        row["record_sha256"],
        row["snapshot_ref"],
        datetime.fromisoformat(row["fetched_at"]),
        row["fields"],
    )
    for row in raw
)
batch = CollectedPolicyBatch("youthcenter", uuid4().hex, datetime.now(timezone.utc), records)
private_json(
    root / "stage1-checkpoint.json",
    {
        "run_id": batch.run_id,
        "previous_run_id": latest,
        "state": "prepared",
        "count": len(records),
        "source": "previously_fetched_official_api_snapshot",
    },
)
result = PostgresCollectedPolicies(url).save(batch, expected_previous_run_id=latest)
with psycopg.connect(url, connect_timeout=5, options="-c statement_timeout=15000") as connection:
    after = dict(
        connection.execute(
            "SELECT policy_id,approved_sha256 FROM public.policy_catalog_releases WHERE publication_status='published'"
        ).fetchall()
    )
    stored = connection.execute(
        "SELECT count(*) FROM public.collected_policies WHERE source='youthcenter' AND missing_detected_at IS NULL"
    ).fetchone()[0]
report = {
    "stage": 1,
    "run_id": batch.run_id,
    "state": "committed",
    "actual_production_postgres": True,
    "source": "previously_fetched_official_api_snapshot",
    "fresh_api_fetch": False,
    "count": len(records),
    "stored": stored,
    "result": asdict(result),
    "existing_catalog_preserved": before == after,
    "model_calls": 0,
    "ai_review_calls": 0,
    "at": datetime.now(timezone.utc).isoformat(),
}
private_json(root / "stage1-report.json", report)
private_json(root / "stage1-checkpoint.json", report)
print(json.dumps(report), flush=True)
