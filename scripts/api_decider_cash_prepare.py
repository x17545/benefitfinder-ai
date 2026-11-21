"""Compile all retained Decider drafts into source-bound cash candidates.

Only reads the configured database when an input snapshot has not yet been
exported. Publishing uses the existing api_decider_expense_publish.py command.
"""

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.decider_client import private_json
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord,
    record_hash,
    validate_source_text,
)
from benefitfinder.ingestion.api_decider_update import decider_update_summary
from benefitfinder.ingestion.decider_fixed_cash import compile_decider_fixed_cash
from benefitfinder.ingestion.decider_reimbursement_cash import compile_decider_reimbursement_cash
from benefitfinder.ingestion.source_cash import compile_source_cash
from benefitfinder.ingestion.source_cash_dynamic import compile_source_cash_dynamic

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--model-results", required=True, type=Path)
parser.add_argument("--regions", required=True, type=Path)
parser.add_argument("--corrections", type=Path)
args = parser.parse_args()
root = args.output
root.mkdir(parents=True, exist_ok=True, mode=0o700)
if not all((root / name).exists() for name in ("drafts.json", "sources.json", "baseline.json")):
    with psycopg.connect(
        os.environ["POLICY_PUBLISH_DATABASE_URL"],
        connect_timeout=5,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on",
    ) as connection:
        drafts = connection.execute("""SELECT r.policy_id,r.document_version,r.approved_sha256,l.external_id
            FROM public.policy_catalog_releases r JOIN public.policy_catalog_source_links l USING(policy_id,document_version)
            WHERE r.publication_status='draft' AND r.payload#>>'{benefit,domain_data,decider_analysis,question_version}'='api-first-locate-decide-v2'""").fetchall()
        baseline = connection.execute(
            "SELECT policy_id,document_version,approved_sha256,review_type,payload FROM public.policy_catalog_releases WHERE publication_status='published'"
        ).fetchall()
        sources = connection.execute("""SELECT p.external_id,p.material_sha256,p.fields,max(r.completed_at) AS accepted_at
            FROM public.collected_policies p LEFT JOIN public.collected_policy_changes h ON h.source=p.source AND h.external_id=p.external_id AND h.current_sha256=p.material_sha256
            LEFT JOIN public.collected_policy_runs r ON r.source=h.source AND r.run_id=h.run_id
            WHERE p.source='youthcenter' GROUP BY p.external_id,p.material_sha256,p.fields""").fetchall()
    private_json(root / "drafts.json", drafts)
    private_json(root / "baseline.json", baseline)
    private_json(root / "sources.json", json.loads(json.dumps(sources, default=str)))
drafts = json.loads((root / "drafts.json").read_text())
draft_count = len(drafts)
if args.corrections:
    seen = {row["external_id"] for row in drafts}
    drafts.extend(
        row for row in json.loads(args.corrections.read_text()) if row["external_id"] not in seen
    )
sources = {row["external_id"]: row for row in json.loads((root / "sources.json").read_text())}
regions = tuple(
    RegionOption(r["sido_code"], r["sido_name"], tuple(DistrictOption(**d) for d in r["sigungu"]))
    for r in json.loads(args.regions.read_text())
)

compilers = (
    compile_decider_reimbursement_cash,
    compile_decider_fixed_cash,
    compile_source_cash_dynamic,
    compile_source_cash,
)
counts, reasons, compiler_counts = Counter(), Counter(), Counter()
entries = {}
prepared = []
as_of = datetime.now(ZoneInfo("Asia/Seoul")).date()
for row in drafts:
    key, pid = row["external_id"], row["policy_id"]
    attempts = []
    try:
        path = args.model_results / (key + ".json")
        artifact = json.loads(path.read_text())
        source = sources[key]
        if artifact["material_sha256"] != source["material_sha256"]:
            raise ValueError("decider_cash_source_changed")
        fields = artifact["source_fields"]
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
            artifact["source_projection"],
            True,
        )
        value = PolicyAnalysisInput(
            pid,
            (doc,),
            (AnalysisPolicy(pid, fields["plcyNm"].strip(), categories),),
            (AnalysisSource("youthcenter", key, source["material_sha256"]),),
        )
        summary = decider_update_summary(artifact, artifact_ref=str(path))
        result = None
        selected = None
        for compiler in compilers:
            candidate = (
                compiler(value, summary, as_of=as_of, regions=regions)
                if compiler.__name__.startswith("compile_decider_")
                else compiler(value, as_of=as_of, regions=regions)
            )
            attempts.append({"compiler": compiler.__name__, "reasons": list(candidate.reasons)})
            if candidate.payload is not None:
                result, selected = candidate, compiler.__name__
                break
        if result is None:
            counts["not_calculable"] += 1
            reasons.update(reason for item in attempts for reason in item["reasons"])
            entries[key] = {"status": "not_calculable", "attempts": attempts}
            continue
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, doc.text)
        output = {
            "policy_id": pid,
            "external_id": key,
            "catalog": record.model_dump(mode="json"),
            "record_sha256": record_hash(record),
            "source_text": doc.text,
            "normalized": result.normalized,
            "actual_local_decider": True,
            "ai_review_calls": 0,
            "compiler": selected,
        }
        private_json(root / "expense-prepared" / (key + ".json"), output)
        entries[key] = {
            "status": "prepared",
            "compiler": selected,
            "record_sha256": record_hash(record),
            "source_version": record.benefit.source.document_version,
        }
        prepared.append(key)
        counts["prepared"] += 1
        compiler_counts[selected] += 1
    except Exception as error:
        code = str(error)
        reason = (
            code if code.startswith("decider_cash_") and len(code) < 100 else type(error).__name__
        )
        entries[key] = {"status": "failed", "reason": reason, "attempts": attempts}
        counts["failed"] += 1
        reasons[reason] += 1
report = {
    "target_drafts": draft_count,
    "additional_corrections": len(drafts) - draft_count,
    "counts": dict(counts),
    "compiler_counts": dict(compiler_counts),
    "reasons": dict(reasons),
    "prepared_ids": prepared,
    "entries": entries,
    "actual_local_decider_outputs": True,
    "ai_review_calls": 0,
    "at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
}
private_json(root / "expense-preparation-report.json", report)
print(
    json.dumps(
        {key: value for key, value in report.items() if key not in {"entries", "prepared_ids"}},
        ensure_ascii=False,
    ),
    flush=True,
)
