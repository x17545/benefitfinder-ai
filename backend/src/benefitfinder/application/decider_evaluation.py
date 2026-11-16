"""Source-bound extraction evaluation; mocks never authorize publication."""

from __future__ import annotations

from benefitfinder.application.decider import (
    KINDS, MODEL_ID, MODEL_REVISION, QUESTION_VERSION, canonical_hash,
)

REQUIRED_CASES = {"fixed", "cap", "exceptions", "missing", "contradiction", "overlap"}
REQUIRED_DECISIONS = {"scope", "coverage", "amount_kind", "eligibility_kind", "overlap_kind"}
EVALUATION_CRITERIA_VERSION = "decider-evaluation-v3"
EVALUATION_THRESHOLDS = {
    "precision": 0.95,
    "recall": 0.20,
    "decision_accuracy": 0.40,
}
MVP_DIAGNOSTIC_GATES = (
    "precision", "recall", "decision_accuracy", "gold_coverage",
    "reference_fields_complete", "sample_count", "features",
    "required_decisions", "decision_sample_counts", "failed_policies",
)


def _is_actual_model(model: dict) -> bool:
    return (model.get("id") == MODEL_ID
            and model.get("revision") == MODEL_REVISION
            and model.get("questions") == QUESTION_VERSION
            and model.get("provider") in {"local", "external_api"})


def evaluate_report(
    report: dict, gold: dict, extractions: dict[str, dict], *, mode: str = "strict",
) -> dict:
    if mode not in {"strict", "mvp"}:
        raise ValueError("decider_invalid_validation_mode")
    entries = {entry["policy_id"]: entry for entry in report["entries"]}
    expected_cases = gold["cases"]
    if (gold.get("format_version") != 1 or not isinstance(expected_cases, list)
        or (not expected_cases and mode == "strict")
        or len({case["policy_id"] for case in expected_cases}) != len(expected_cases)):
        raise ValueError("decider_invalid_gold")
    totals = {"expected_characters": 0, "predicted_characters": 0, "matched_characters": 0,
              "decision_checks": 0, "correct_decisions": 0, "failed_policies": 0}
    decision_counts = {}
    features = set()
    for case in expected_cases:
        policy = case["policy_id"]
        entry = entries[policy]
        if case["input_sha256"] != entry["input_sha256"]:
            raise ValueError("decider_gold_source_mismatch")
        features.update(case["features"])
        extraction = None if entry['status'] == 'failed' else extractions.get(policy)
        if extraction is None:
            totals["failed_policies"] += 1
            for doc in case['documents']:
                totals['expected_characters'] += len({(s['kind'], c) for s in doc['spans']
                                                      for c in range(s['start'], s['end'])})
                totals['decision_checks'] += len(doc['decisions'])
                for key in doc['decisions']:
                    decision_counts.setdefault(key, {'checks': 0, 'correct': 0})['checks'] += 1
            continue
        documents = {doc["document_id"]: doc for doc in extraction["documents"]}
        for expected in case["documents"]:
            actual = documents[expected["document_id"]]
            predicted = set()
            target = set()
            # Code-read fields and deterministic context are not model recall.
            for span in actual.get("raw_spans", actual["spans"]):
                predicted.update((span["kind"], c) for c in range(span["start"], span["end"]))
            for span in expected["spans"]:
                if type(span["start"]) is not int or type(span["end"]) is not int or span["start"] >= span["end"]:
                    raise ValueError("decider_invalid_gold_span")
                target.update((span["kind"], c) for c in range(span["start"], span["end"]))
            totals["expected_characters"] += len(target)
            totals["predicted_characters"] += len(predicted)
            totals["matched_characters"] += len(target & predicted)
            for key, expected_value in expected["decisions"].items():
                totals["decision_checks"] += 1
                accepted = expected_value if isinstance(expected_value, list) else [expected_value]
                correct = actual["decisions"].get(key) in accepted
                totals["correct_decisions"] += correct
                decision_counts.setdefault(key, {'checks': 0, 'correct': 0})['checks'] += 1
                decision_counts[key]['correct'] += correct
    precision = totals["matched_characters"] / max(1, totals["predicted_characters"])
    recall = totals["matched_characters"] / max(1, totals["expected_characters"])
    accuracy = totals["correct_decisions"] / max(1, totals["decision_checks"])
    actual_model = _is_actual_model(report["model"])
    decisions = {key: {**counts, 'accuracy': counts['correct'] / counts['checks']}
                 for key, counts in decision_counts.items()}
    coverage = gold.get("coverage", "full")
    complete_reference = all(set(case.get('reviewed_fields', [])) == set(KINDS)
                             for case in expected_cases)
    return {"format_version": 1, "criteria_version": EVALUATION_CRITERIA_VERSION,
            "thresholds": dict(EVALUATION_THRESHOLDS),
            "mode": mode, "model_quality_gate": "diagnostic" if mode == "mvp" else "strict",
            "relaxed_global_gates": list(MVP_DIAGNOSTIC_GATES) if mode == "mvp" else [],
            "model": report["model"], "gold_sha256": canonical_hash(gold),
            "gold_coverage": coverage, "reference_fields_complete": complete_reference,
            "sample_count": len(expected_cases), "features": sorted(features), "totals": totals,
            "decisions": decisions,
            "precision": precision, "recall": recall, "decision_accuracy": accuracy,
            "eligible_for_publication": (coverage == "full" and complete_reference
                and actual_model and len(expected_cases) >= 10
                and REQUIRED_CASES <= features and totals["failed_policies"] == 0
                and precision >= EVALUATION_THRESHOLDS["precision"]
                and recall >= EVALUATION_THRESHOLDS["recall"]
                and accuracy >= EVALUATION_THRESHOLDS["decision_accuracy"]
                and REQUIRED_DECISIONS <= decisions.keys()
                and all(decisions[key]['checks'] >= len(expected_cases)
                        for key in REQUIRED_DECISIONS))}


def require_evaluation(value: dict, model: dict, *, mode: str = "strict") -> None:
    # Provider-specific batching can change BF16 results; evaluate the actual path.
    if mode not in {"strict", "mvp"}:
        raise ValueError("decider_invalid_validation_mode")
    if value.get("model") != model or not _is_actual_model(model):
        raise ValueError("decider_evaluation_not_passed")
    # MVP quality metrics are diagnostic; policy source/payment validation remains mandatory.
    if mode == "mvp":
        return
    if (value.get("eligible_for_publication") is not True
        or value.get("format_version") != 1
        or value.get("criteria_version") != EVALUATION_CRITERIA_VERSION
        or value.get("thresholds") != EVALUATION_THRESHOLDS
        or value.get("gold_coverage") != "full"
        or value.get("reference_fields_complete") is not True
        or type(value.get("sample_count")) is not int or value["sample_count"] < 10
        or value.get("totals", {}).get("failed_policies") != 0
        or not REQUIRED_CASES <= set(value.get("features", []))
        or not value.get('decisions')
        or not REQUIRED_DECISIONS <= value.get('decisions', {}).keys()
        or any(value['decisions'][key].get('checks', 0) < value.get('sample_count', 0)
               for key in REQUIRED_DECISIONS)
        or any(type(value.get(k)) not in (float, int)
               or not EVALUATION_THRESHOLDS[k] <= value[k] <= 1
               for k in ("precision", "recall", "decision_accuracy"))):
        raise ValueError("decider_evaluation_not_passed")
