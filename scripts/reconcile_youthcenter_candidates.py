"""Reconcile saved youthcenter records; produce candidates, never approved policies."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def reconcile(records: list[dict[str, Any]], prior_ids: list[str]) -> dict[str, Any]:
    """Compare IDs and flag exact normalized titles; neither filter establishes eligibility."""
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        policy_id = record["external_id"]
        if not isinstance(policy_id, str) or not policy_id or policy_id in indexed:
            raise ValueError("duplicate_or_invalid_source_id")
        indexed[policy_id] = record
    if len(set(prior_ids)) != len(prior_ids):
        raise ValueError("duplicate_prior_id")

    candidates: list[str] = []
    titles: dict[str, list[str]] = defaultdict(list)
    for policy_id, record in indexed.items():
        fields = record["fields"]
        category = fields.get("lclsfNm") or ""
        regions = (fields.get("zipCd") or "").split(",")
        if not any(name in category for name in ("일자리", "주거")):
            continue
        if not any(re.fullmatch(r"48\d{3}", region.strip()) for region in regions):
            continue
        candidates.append(policy_id)
        title = " ".join(unicodedata.normalize("NFKC", fields.get("plcyNm") or "").split())
        if title:
            titles[title.casefold()].append(policy_id)
    candidate_ids = set(candidates)
    return {
        "source_records": len(records),
        "candidate_ids": sorted(candidates),
        "prior_ids_missing_from_source": sorted(set(prior_ids) - indexed.keys()),
        "prior_ids_outside_filter": sorted((set(prior_ids) & indexed.keys()) - candidate_ids),
        "same_title_candidates": [sorted(ids) for ids in titles.values() if len(ids) > 1],
        "scope_confirmed": False,
        "duplicates_confirmed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Saved JSON array of public source records")
    parser.add_argument("prior_ids", type=Path, help="Previous policy IDs, one per line")
    args = parser.parse_args()
    records = json.loads(args.source.read_text(encoding="utf-8"))
    prior = [line.strip() for line in args.prior_ids.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps(reconcile(records, prior), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
