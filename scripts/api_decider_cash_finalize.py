"""Retire only original drafts whose exact replacement is already published."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from benefitfinder.infrastructure.decider_client import private_json
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash


parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--publish", action="store_true")
args = parser.parse_args()
root = args.output
drafts = {row["external_id"]: row for row in json.loads((root / "drafts.json").read_text())}
published = json.loads((root / "expense-production/report.json").read_text())
items = []
for external_id, entry in published["entries"].items():
    if entry["status"] != "published" or external_id not in drafts:
        continue
    draft = drafts[external_id]
    actual = json.loads((root / "expense-production/prepared" / (external_id + ".json")).read_text())
    replacement = CatalogRecord.model_validate(actual["catalog"])
    if replacement.benefit.id != draft["policy_id"]:
        raise ValueError("cash_finalize_policy_identity_mismatch")
    items.append(
        {
            "policy_id": draft["policy_id"],
            "document_version": draft["document_version"],
            "old_hash": draft["approved_sha256"],
            "new_hash": record_hash(replacement),
        }
    )

bindings = """WITH items AS (
    SELECT * FROM jsonb_to_recordset(%s::jsonb)
    AS t(policy_id text,document_version text,old_hash text,new_hash text)
) """
proof = """d.policy_id=i.policy_id AND d.document_version=i.document_version
    AND d.approved_sha256=i.old_hash AND current.policy_id=i.policy_id
    AND current.publication_status='published' AND current.review_type='ai'
    AND current.approved_sha256=i.new_hash"""
with psycopg.connect(
    os.environ["POLICY_PUBLISH_DATABASE_URL"],
    row_factory=dict_row,
    connect_timeout=5,
    options="-c statement_timeout=8000"
    + ("" if args.publish else " -c default_transaction_read_only=on"),
) as connection:
    if args.publish:
        changed = connection.execute(
            bindings
            + """UPDATE public.policy_catalog_releases d SET publication_status='retired'
            FROM items i,public.policy_catalog_releases current WHERE """
            + proof
            + " AND d.publication_status='draft' RETURNING d.policy_id",
            (Jsonb(items),),
        ).fetchall()
    else:
        changed = []
    completed = connection.execute(
        bindings
        + """SELECT d.policy_id,d.document_version,d.approved_sha256,d.publication_status
        FROM public.policy_catalog_releases d,items i,public.policy_catalog_releases current
        WHERE """
        + proof
        + " AND d.publication_status IN ('draft','retired')",
        (Jsonb(items),),
    ).fetchall()

report = {
    "actual_production_write": args.publish,
    "newly_retired": len(changed),
    "completed": sum(row["publication_status"] == "retired" for row in completed),
    "ready_to_retire": sum(row["publication_status"] == "draft" for row in completed),
    "kept_as_immutable_history": True,
    "entries": completed,
    "at": datetime.now(timezone.utc).isoformat(),
}
private_json(root / "cash-finalization-report.json", report)
print(json.dumps({key: value for key, value in report.items() if key != "entries"}), flush=True)
