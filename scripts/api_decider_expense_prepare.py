"""Compile source-bound expense candidates from actual offline Decider artifacts."""

import argparse
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
from benefitfinder.application.policy_analysis import (
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import RegionOption, DistrictOption
from benefitfinder.infrastructure.decider_client import private_json
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.ingestion.api_decider_update import QUESTION_VERSION, decider_update_summary
from benefitfinder.ingestion.decider_expense_cash import compile_decider_expense_cash

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--regions", type=Path, required=True)
args = parser.parse_args()
root = args.output
regions = tuple(
    RegionOption(r["sido_code"], r["sido_name"], tuple(DistrictOption(**d) for d in r["sigungu"]))
    for r in json.loads(args.regions.read_text())
)
counts = Counter()
reasons = Counter()
prepared = []
for path in sorted((root / "model-results").glob("*.json")):
    artifact = json.loads(path.read_text())
    if artifact.get("question_version") != QUESTION_VERSION:
        continue
    key = artifact["external_id"]
    fields = artifact["source_fields"]
    pid = "youth:youthcenter:" + key
    try:
        categories = tuple(
            k
            for label, k in (("일자리", "employment"), ("주거", "housing"))
            if label in str(fields.get("lclsfNm", "")).split(",")
        )
        doc = SourceDocument(
            "youthcenter:" + key + ":api-decider-v2",
            pid,
            "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + key,
            "sha256:" + artifact["material_sha256"],
            datetime.fromisoformat(artifact["source_fetched_at"]),
            artifact["source_projection"],
            True,
        )
        value = PolicyAnalysisInput(
            pid,
            (doc,),
            (AnalysisPolicy(pid, fields["plcyNm"].strip(), categories),),
            (AnalysisSource("youthcenter", key, artifact["material_sha256"]),),
        )
        summary = decider_update_summary(artifact, artifact_ref=str(path))
        result = compile_decider_expense_cash(
            value, summary, as_of=datetime.now(ZoneInfo("Asia/Seoul")).date(), regions=regions
        )
        if result.payload is None:
            counts["not_calculable"] += 1
            reasons.update(result.reasons)
            continue
        record = CatalogRecord.model_validate(result.payload)
        output = {
            "policy_id": pid,
            "external_id": key,
            "catalog": record.model_dump(mode="json"),
            "record_sha256": record_hash(record),
            "input": value.payload(),
            "source_text": doc.text,
            "normalized": result.normalized,
            "actual_local_decider": True,
            "ai_review_calls": 0,
        }
        private_json(root / "expense-prepared" / (key + ".json"), output)
        prepared.append(key)
        counts["prepared"] += 1
    except Exception as error:
        counts["failed"] += 1
        reasons.update([type(error).__name__])
report = {
    "counts": dict(counts),
    "reasons": dict(reasons),
    "prepared_ids": prepared,
    "actual_local_decider_outputs": True,
    "ai_review_calls": 0,
}
private_json(root / "expense-preparation-report.json", report)
print(json.dumps(report), flush=True)
