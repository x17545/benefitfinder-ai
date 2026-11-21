"""Publish amount-unknown records or direct GPT parser results without AI review."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch
from benefitfinder.infrastructure.database.repositories.decider_updates import (
    publish_model_catalog_updates,
)
from benefitfinder.infrastructure.decider_client import private_json
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.ingestion.api_decider_update import decider_update_summary
from benefitfinder.ingestion.mvp_cash_import import compile_mvp_cash_import


parser = argparse.ArgumentParser()
parser.add_argument("--selection", choices=("unknown", "parsed"), required=True)
parser.add_argument("--mode", choices=("prepare", "isolated", "publish"), default="prepare")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--input-run", type=Path, required=True)
parser.add_argument("--model-results", type=Path, required=True)
parser.add_argument("--regions", type=Path, required=True)
parser.add_argument("--batch-size", type=int, default=32, choices=range(1, 33))
args = parser.parse_args()
root = args.output / args.selection
root.mkdir(parents=True, exist_ok=True, mode=0o700)
old = args.input_run
prior = json.loads((old / "expense-preparation-report.json").read_text())
money = {row["external_id"] for row in json.loads((old / "money-cases.json").read_text())}
targets = [
    row
    for row in json.loads((old / "drafts.json").read_text())
    if prior["entries"][row["external_id"]]["status"] == "not_calculable"
    and (row["external_id"] in money) == (args.selection == "parsed")
]
original_drafts = {row["external_id"]: row for row in targets}
sources = {row["external_id"]: row for row in json.loads((old / "sources.json").read_text())}
regions = tuple(
    RegionOption(
        row["sido_code"], row["sido_name"], tuple(DistrictOption(**d) for d in row["sigungu"])
    )
    for row in json.loads(args.regions.read_text())
)
parsed = {}
for path in sorted((args.output / "parsed").glob("batch-*.json")):
    for item in json.loads(path.read_text()):
        parsed[item["external_id"]] = (item, str(path))

with psycopg.connect(
    os.environ["POLICY_PUBLISH_DATABASE_URL"],
    row_factory=dict_row,
    options="-c default_transaction_read_only=on -c statement_timeout=15000",
    connect_timeout=5,
) as connection:
    before = connection.execute("""SELECT r.policy_id,r.document_version,r.approved_sha256,
        r.review_type,r.payload,l.external_id FROM public.policy_catalog_releases r
        LEFT JOIN public.policy_catalog_source_links l USING(policy_id,document_version)
        WHERE r.publication_status='published'""").fetchall()
    missing = {
        row["external_id"]: row["missing_detected_at"].isoformat()
        for row in connection.execute(
            "SELECT external_id,missing_detected_at FROM public.collected_policies "
            "WHERE source='youthcenter' AND missing_detected_at IS NOT NULL"
        ).fetchall()
    }
if not (root / "before-releases.json").exists():
    private_json(root / "before-releases.json", before)
current = {row["external_id"]: row for row in before if row["external_id"]}
entries, prepared = {}, []
as_of = datetime.now(ZoneInfo("Asia/Seoul")).date()
for row in targets:
    key = row["external_id"]
    try:
        previous = current.get(key)
        if previous and previous["review_type"] not in {"unreviewed", "ai"}:
            entries[key] = {"status": "protected_existing"}
            continue
        # Previous calculation-ready releases always take precedence over this import.
        if previous and previous["payload"]["benefit"].get("reviewed"):
            same_import = (
                previous["payload"]["benefit"].get("domain_data", {}).get("gpt6_analysis")
                and args.selection == "parsed"
            )
            if not same_import:
                entries[key] = {"status": "protected_existing"}
                continue
        if args.selection == "parsed" and key not in parsed:
            entries[key] = {"status": "awaiting_parser"}
            continue
        pid = previous["policy_id"] if previous else row["policy_id"]
        path = args.model_results / (key + ".json")
        native = json.loads(path.read_text())
        source = sources[key]
        if native["material_sha256"] != source["material_sha256"]:
            raise ValueError("mvp_source_changed")
        fields = native["source_fields"]
        categories = tuple(
            category
            for label, category in (("일자리", "employment"), ("주거", "housing"))
            if label in str(fields.get("lclsfNm", "")).split(",")
        )
        doc = SourceDocument(
            "youthcenter:" + key + ":api-decider-v2",
            pid,
            "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + key,
            "sha256:" + source["material_sha256"],
            datetime.fromisoformat(source["accepted_at"]),
            native["source_projection"],
            True,
        )
        value = PolicyAnalysisInput(
            pid,
            (doc,),
            (AnalysisPolicy(pid, fields["plcyNm"].strip(), categories),),
            (AnalysisSource("youthcenter", key, source["material_sha256"]),),
        )
        item, parsed_path = (
            parsed.get(key, (None, None)) if args.selection == "parsed" else (None, None)
        )
        result = compile_mvp_cash_import(
            value,
            decider_update_summary(native, artifact_ref=str(path)),
            as_of=as_of,
            parsed=item,
            artifact_ref=parsed_path,
            regions=regions,
            source_missing_at=missing.get(key),
        )
        if result.payload is None:
            raise ValueError(result.reasons[0])
        record = CatalogRecord.model_validate(result.payload)
        expected = previous["approved_sha256"] if previous else None
        private_json(
            root / "prepared" / (key + ".json"),
            {
                "external_id": key,
                "catalog": record.model_dump(mode="json"),
                "source_text": doc.text,
                "expected_previous": expected,
                "old_draft": row,
            },
        )
        entries[key] = {
            "status": "prepared",
            "policy_id": pid,
            "sha256": record_hash(record),
            "calculation_ready": record.benefit.reviewed,
        }
        if (
            args.mode == "publish"
            and previous
            and previous["approved_sha256"] == record_hash(record)
        ):
            entries[key].update(status="published", replayed=True)
        else:
            prepared.append((key, record, doc.text, expected))
    except Exception as error:
        entries[key] = {"status": "failed", "reason": type(error).__name__}


def report():
    result = {
        "selection": args.selection,
        "mode": args.mode,
        "targets": len(targets),
        "counts": dict(Counter(item["status"] for item in entries.values())),
        "calculation_ready": sum(
            item.get("calculation_ready", False)
            for item in entries.values()
            if item["status"] in {"prepared", "published"}
        ),
        "ai_review_calls": 0,
        "entries": entries,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    private_json(root / (args.mode + "-report.json"), result)
    print(json.dumps({k: v for k, v in result.items() if k != "entries"}), flush=True)


if args.mode == "prepare":
    report()
    raise SystemExit(0)
url = os.environ[
    "POLICY_PUBLISH_DATABASE_URL" if args.mode == "publish" else "F13_TEST_DATABASE_URL"
]
if args.mode == "isolated":
    settings = conninfo_to_dict(url)
    if settings.get("host") not in {"localhost", "127.0.0.1"} or not settings.get(
        "dbname", ""
    ).endswith("_test"):
        parser.error("isolated_test_database_required")


def publish(batch):
    for key, record, _, _ in batch:
        if key not in missing:
            continue
        digest = record_hash(record)
        provenance = {
            "document_version": record.benefit.source.document_version,
            "source_sha256": record.source_sha256,
            "record_sha256": digest,
        }
        with psycopg.connect(url, connect_timeout=5) as connection:
            connection.execute("SET LOCAL statement_timeout='8s'")
            source = record.collection_sources[0]
            observed = connection.execute(
                "SELECT material_sha256,missing_detected_at FROM public.collected_policies "
                "WHERE source=%s AND external_id=%s FOR SHARE",
                (source.source, source.external_id),
            ).fetchone()
            if not observed or observed[0] != source.material_sha256 or observed[1] is None:
                raise ValueError("mvp_missing_source_history_changed")
            connection.execute(
                """INSERT INTO public.policy_catalog_releases
                (policy_id,document_version,payload,approved_sha256,review_status,publication_status,
                 review_type,unreviewed_source) VALUES (%s,%s,%s,%s,'unreviewed','draft','unreviewed',%s)
                 ON CONFLICT (policy_id,document_version) DO NOTHING""",
                (
                    record.benefit.id,
                    record.benefit.source.document_version,
                    Jsonb(record.model_dump(mode="json")),
                    digest,
                    Jsonb(provenance),
                ),
            )
            stored = connection.execute(
                "SELECT approved_sha256 FROM public.policy_catalog_releases "
                "WHERE policy_id=%s AND document_version=%s AND publication_status='draft'",
                (record.benefit.id, record.benefit.source.document_version),
            ).fetchone()
            if not stored or stored[0] != digest:
                raise ValueError("mvp_missing_source_history_conflict")
        entries[key].update(status="stored_source_missing", replayed=True)
    unknown = [
        (r, text, expected if args.mode == "publish" else None)
        for key, r, text, expected in batch
        if not r.benefit.reviewed and key not in missing
    ]
    cash = []
    expectations = {}
    for _, record, text, expected in batch:
        if not record.benefit.reviewed:
            continue
        review = AIReview(
            writer_agent="gpt-6-astra:youth-cash-parse-v1",
            reviewer_agent="user-authorized-direct-publication",
            reviewer_model="none:no-ai-review",
            verdict="pass",
            reviewed_at=datetime.now(timezone.utc),
            document_version=record.benefit.source.document_version,
            source_sha256=record.source_sha256,
            record_sha256=record_hash(record),
        )
        cash.append((record, review, text, None))
        if expected and args.mode == "publish":
            expectations[record.benefit.id] = expected
    for _ in range(2):
        if unknown:
            publish_model_catalog_updates(url, unknown)
        if cash:
            publish_ai_batch(
                url,
                cash,
                allow_replace_ai=True,
                allow_replace_unreviewed=True,
                expected_previous=expectations,
            )
    with psycopg.connect(url, connect_timeout=5) as connection:
        observed = dict(
            connection.execute(
                "SELECT policy_id,approved_sha256 FROM public.policy_catalog_releases "
                "WHERE publication_status='published' AND policy_id=ANY(%s)",
                ([record.benefit.id for _, record, _, _ in batch],),
            ).fetchall()
        )
    for key, record, _, _ in batch:
        if key in missing:
            continue
        entries[key]["status"] = (
            "published"
            if observed.get(record.benefit.id) == record_hash(record)
            else "protected_existing"
        )
        entries[key]["replayed"] = entries[key]["status"] == "published"


def retire_completed():
    completed = [
        {
            "policy_id": original_drafts[key]["policy_id"],
            "document_version": original_drafts[key]["document_version"],
            "old_hash": original_drafts[key]["approved_sha256"],
            "new_policy_id": entry["policy_id"],
            "new_hash": entry["sha256"],
            "new_status": "draft" if entry["status"] == "stored_source_missing" else "published",
        }
        for key, entry in entries.items()
        if entry["status"] in {"published", "stored_source_missing"}
    ]
    with psycopg.connect(url, connect_timeout=5) as connection:
        connection.execute("SET LOCAL statement_timeout='8s'")
        connection.execute(
            """WITH items AS (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS t(
                    policy_id text,document_version text,old_hash text,new_policy_id text,new_hash text,
                    new_status text)
                ) UPDATE public.policy_catalog_releases d SET publication_status='retired'
                FROM items i,public.policy_catalog_releases current
                WHERE d.policy_id=i.policy_id AND d.document_version=i.document_version
                AND d.approved_sha256=i.old_hash AND d.publication_status='draft'
                AND current.policy_id=i.new_policy_id AND current.publication_status=i.new_status
                AND current.approved_sha256=i.new_hash""",
            (Jsonb(completed),),
        )


for start in range(0, len(prepared), args.batch_size):
    batch = prepared[start : start + args.batch_size]
    try:
        publish(batch)
    except Exception:
        for item in batch:
            try:
                publish([item])
            except Exception as error:
                entries[item[0]].update(status="failed", reason=type(error).__name__)
    report()
if args.mode == "publish":
    retire_completed()
report()
