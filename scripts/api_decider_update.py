"""Store offline API Decider artifacts in F12 and immutable catalog releases.

Use --publish for the configured production database. The default destination
is an isolated local PostgreSQL database with fictional credentials.
"""

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.decider_client import private_json
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord
from benefitfinder.ingestion.api_decider_update import (
    QUESTION_VERSION,
    compile_api_decider_update,
    decider_update_summary,
)
from benefitfinder.infrastructure.database.repositories.decider_updates import (
    store_decider_analyses,
    publish_model_catalog_updates,
)

parser = argparse.ArgumentParser()
parser.add_argument("--publish", action="store_true")
parser.add_argument("--catalog-updates", action="store_true")
parser.add_argument("--once", action="store_true")
parser.add_argument("--limit", type=int, default=0)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--max-seconds", type=int, default=3600)
args = parser.parse_args()
if not 30 <= args.max_seconds <= 86400:
    parser.error("invalid_run_time_limit")
deadline = time.time() + args.max_seconds
ROOT = args.output
url = (
    os.environ["POLICY_PUBLISH_DATABASE_URL"]
    if args.publish
    else os.environ.get(
        "F13_TEST_DATABASE_URL",
        "postgresql://postgres:isolated-rapid-test@127.0.0.1:55439/rapid_update_test",
    )
)
phase = ROOT / ("stage2-production" if args.publish else "stage2-isolated")
phase.mkdir(parents=True, exist_ok=True, mode=0o700)
state_path = phase / "entries.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}


def safe(error):
    text = str(error)
    return (
        text
        if text.startswith(
            ("api_decider_", "decider_update_", "unreviewed_", "catalog_", "ai_catalog_", "f12_")
        )
        and len(text) < 140
        and "\n" not in text
        else type(error).__name__
    )


if not args.publish:
    from benefitfinder.application.collection import CollectedRecord
    from benefitfinder.application.collected_policies import CollectedPolicyBatch
    from benefitfinder.infrastructure.database.repositories.collected_policies import (
        PostgresCollectedPolicies,
    )
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg import sql

    test_settings = conninfo_to_dict(url)
    test_name = test_settings.get("dbname", "")
    if not test_name.endswith("_test") or test_settings.get("host") not in {
        "127.0.0.1",
        "localhost",
    }:
        parser.error("isolated_test_database_required")
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (test_name,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_name)))
    with psycopg.connect(url) as c:
        if not c.execute("SELECT to_regclass('public.benefitfinder_schema_migrations')").fetchone()[
            0
        ]:
            for path in sorted(Path("backend/migrations/versions").glob("*.sql")):
                c.execute(path.read_text())
    with psycopg.connect(url) as c:
        exists = c.execute("SELECT count(*) FROM public.collected_policies").fetchone()[0]
    if not exists:
        raw = json.loads((ROOT / "api-originals.json").read_text())
        records = tuple(
            CollectedRecord(
                r["source_name"],
                r["external_id"],
                r["record_sha256"],
                r["snapshot_ref"],
                datetime.fromisoformat(r["fetched_at"]),
                r["fields"],
            )
            for r in raw
        )
        PostgresCollectedPolicies(url).save(
            CollectedPolicyBatch("youthcenter", uuid4().hex, datetime.now(timezone.utc), records)
        )
with psycopg.connect(
    url, connect_timeout=5, options="-c statement_timeout=15000", row_factory=dict_row
) as c:
    accepted = {
        r["external_id"]: r["accepted_at"]
        for r in c.execute(
            "SELECT p.external_id,max(r.completed_at) AS accepted_at FROM public.collected_policies p JOIN public.collected_policy_changes h ON h.source=p.source AND h.external_id=p.external_id AND h.current_sha256=p.material_sha256 JOIN public.collected_policy_runs r ON r.source=h.source AND r.run_id=h.run_id WHERE p.source='youthcenter' GROUP BY p.external_id"
        ).fetchall()
    }
    aliases = {
        r["external_id"]: r["policy_id"]
        for r in c.execute(
            "SELECT DISTINCT l.external_id,r.policy_id FROM public.policy_catalog_source_links l JOIN public.policy_catalog_releases r USING(policy_id,document_version) WHERE l.source='youthcenter' AND r.publication_status='published'"
        ).fetchall()
    }
    existing = {
        r["policy_id"]: r
        for r in c.execute(
            "SELECT policy_id,approved_sha256,review_type,payload,document_version FROM public.policy_catalog_releases WHERE publication_status='published'"
        ).fetchall()
    }
if not (phase / "catalog-before.json").exists():
    private_json(phase / "catalog-before.json", list(existing.values()))
for key, item in state.items():
    previous = existing.get(aliases.get(key, "youth:youthcenter:" + key))
    if (
        item.get("error") == "catalog_policy_kind_is_immutable"
        and previous
        and previous["payload"].get("kind") == "service"
    ):
        item.update(
            status="processed",
            catalog_status="protected_existing",
            recovery="existing_service_preserved",
        )
        item.pop("error", None)


def report(complete=False):
    counts = Counter()
    for item in state.values():
        counts["processed"] += 1
        counts["analysis_stored"] += item.get("analysis_stored", False)
        counts["catalog_updated"] += item.get("catalog_status") in (
            "inserted",
            "replaced",
            "replayed",
        )
        counts["catalog_protected"] += item.get("catalog_status") == "protected_existing"
        counts["failed"] += "error" in item
    result = {
        "stage": 2,
        "actual_production_postgres": args.publish,
        "actual_local_decider_outputs": True,
        "question_version": QUESTION_VERSION,
        "counts": dict(counts),
        "catalog_status_counts": dict(
            Counter(e.get("catalog_status", "not_prepared") for e in state.values())
        ),
        "failure_reasons": dict(Counter(e["error"] for e in state.values() if "error" in e)),
        "ai_review_calls": 0,
        "complete": complete,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    private_json(state_path, state)
    private_json(phase / "report.json", result)
    print(json.dumps(result), flush=True)


def send_analyses(batch):
    try:
        result = store_decider_analyses(url, [(v, s, h) for _, v, s, h, _ in batch])
        for key, *_ in batch:
            state[key]["analysis_stored"] = True
        return result
    except Exception as error:
        if len(batch) > 1:
            for item in batch:
                send_analyses([item])
        else:
            key = batch[0][0]
            state[key].update(status="failed", error=safe(error))


def send_catalog(batch):
    try:
        result = publish_model_catalog_updates(url, [(r, t, e) for _, r, t, e in batch])
        # Determine each outcome from the current immutable release after commit.
        with psycopg.connect(url, connect_timeout=5, options="-c statement_timeout=10000") as c:
            for key, record, _, expected in batch:
                current = c.execute(
                    "SELECT approved_sha256,review_type,payload#>>'{benefit,domain_data,decider_analysis,question_version}' FROM public.policy_catalog_releases WHERE policy_id=%s AND publication_status='published'",
                    (record.benefit.id,),
                ).fetchone()
                if current and current[2] == QUESTION_VERSION:
                    state[key]["catalog_status"] = "replaced" if expected else "inserted"
                else:
                    state[key]["catalog_status"] = "protected_existing"
        return result
    except Exception as error:
        if len(batch) > 1:
            for item in batch:
                send_catalog([item])
        else:
            key = batch[0][0]
            state[key].update(catalog_status="failed", error=safe(error))


complete = False
while time.time() < deadline:
    todo = []
    for path in sorted((ROOT / "model-results").glob("*.json")):
        artifact = json.loads(path.read_text())
        if artifact.get("question_version") != QUESTION_VERSION:
            continue
        key = artifact["external_id"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if (
            key in state
            and state[key].get("artifact_sha256") == digest
            and (state[key].get("status") != "failed" or state[key].get("attempts", 0) >= 3)
        ):
            continue
        todo.append((key, path, artifact, digest))
    if args.limit:
        todo = todo[: args.limit]
    for start in range(0, len(todo), 16):
        analyses = []
        catalog = []
        for key, path, artifact, digest in todo[start : start + 16]:
            attempts = (
                (state.get(key, {}).get("attempts", 0) + 1)
                if state.get(key, {}).get("artifact_sha256") == digest
                else 1
            )
            state[key] = {
                "artifact_sha256": digest,
                "status": "processed",
                "analysis_stored": False,
                "catalog_status": "not_prepared",
                "attempts": attempts,
            }
            try:
                fields = artifact["source_fields"]
                pid = aliases.get(key, "youth:youthcenter:" + key)
                categories = tuple(
                    k
                    for label, k in (("일자리", "employment"), ("주거", "housing"))
                    if label in str(fields.get("lclsfNm", "")).split(",")
                )
                text = artifact["source_projection"]
                stamp = accepted[key]
                doc = SourceDocument(
                    "youthcenter:" + key + ":api-decider-v2",
                    pid,
                    "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
                    + key,
                    "sha256:" + artifact["material_sha256"],
                    stamp,
                    text,
                    True,
                )
                value = PolicyAnalysisInput(
                    pid,
                    (doc,),
                    (AnalysisPolicy(pid, fields["plcyNm"].strip(), categories),),
                    (AnalysisSource("youthcenter", key, artifact["material_sha256"]),),
                )
                summary = decider_update_summary(artifact, artifact_ref=str(path))
                analyses.append((key, value, summary, digest, artifact))
                previous = existing.get(pid)
                if previous and (
                    previous["review_type"] != "unreviewed"
                    or previous["payload"].get("kind") == "service"
                ):
                    state[key]["catalog_status"] = "protected_existing"
                    continue
                if previous and (
                    len(previous["payload"].get("collection_sources", [])) != 1
                    or previous["document_version"].endswith(":decider-v3")
                ):
                    state[key]["catalog_status"] = "protected_existing"
                    continue
                compiled = compile_api_decider_update(
                    value,
                    summary,
                    as_of=datetime.now(timezone.utc)
                    .astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul"))
                    .date(),
                )
                if compiled.payload is None:
                    state[key]["catalog_status"] = (
                        "outside_mvp"
                        if "api_decider_catalog_outside_mvp" in compiled.reasons
                        else "not_prepared"
                    )
                    state[key]["reasons"] = list(compiled.reasons)
                    continue
                record = CatalogRecord.model_validate(compiled.payload)
                if args.publish and not args.catalog_updates:
                    # Full native outputs already live in F12. Do not copy the
                    # entire analysis corpus into every online catalog read.
                    private_json(
                        phase / "catalog-candidates" / (key + ".json"),
                        {
                            "catalog": record.model_dump(mode="json"),
                            "source_text": text,
                            "expected_previous": previous["approved_sha256"] if previous else None,
                        },
                    )
                    state[key]["catalog_status"] = "stored_candidate"
                    continue
                catalog.append(
                    (key, record, text, previous["approved_sha256"] if previous else None)
                )
            except Exception as error:
                state[key].update(status="failed", error=safe(error))
        if analyses:
            send_analyses(analyses)
        if catalog:
            send_catalog(catalog)
        report()
    model_report = ROOT / "stage2-model-report.json"
    complete = (
        json.loads(model_report.read_text()).get("complete", False)
        if model_report.exists()
        else False
    )
    if args.once or (complete and not todo):
        break
    time.sleep(2)
report(complete)
