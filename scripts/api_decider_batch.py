"""Run API-first local Decider updates with bounded, resumable GPU batches.

Only official API policy material is accepted. Outputs are offline artifacts;
publication uses the existing F12 and catalogue database writers.
"""

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from benefitfinder.ai.decider_extraction import text_windows
from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.decider_client import (
    LocalDecider,
    CheckpointDecider,
    private_json,
)
from benefitfinder.ingestion.decider_pending import _fields

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--checkpoint", type=Path, default=Path("data/local/decider/checkpoint"))
parser.add_argument("--max-seconds", type=int, default=3600)
parser.add_argument("--batch-size", type=int, default=16)
args = parser.parse_args()
if not 30 <= args.max_seconds <= 86400 or not 1 <= args.batch_size <= 32:
    parser.error("invalid_batch_limits")
ROOT = args.output
VERSION = "api-first-locate-decide-v2"
DEADLINE = time.time() + args.max_seconds
rows = json.loads((ROOT / "api-originals.json").read_text())
before = json.loads((ROOT / "catalog-before.json").read_text())
selected = []
outside = []
for row in rows:
    categories = set(str(row["fields"].get("lclsfNm", "")).split(","))
    if categories & {"일자리", "주거"}:
        selected.append(row)
    else:
        outside.append(row["external_id"])
selected.sort(
    key=lambda r: (
        not any(
            key in before
            for key in ("youth:" + r["external_id"], "youth:youthcenter:" + r["external_id"])
        ),
        len(str(r["fields"].get("plcySprtCn", ""))),
    )
)
private_json(
    ROOT / "stage2-selection.json",
    {
        "targets": len(selected),
        "out_of_mvp": len(outside),
        "outside_ids": outside,
        "question_version": VERSION,
    },
)
client0 = LocalDecider(args.checkpoint, max_length=3072, batch_tokens=3072, locate_threshold=0.2)


class Versioned:
    @property
    def identity(self):
        return client0.identity | {"questions": VERSION}

    def decide(self, requests):
        return client0.decide(requests)


client = CheckpointDecider(Versioned(), ROOT / "model-responses")
print(
    json.dumps(
        {
            "phase": "loaded",
            "targets": len(selected),
            "out_of_mvp": len(outside),
            "model": client.identity,
        }
    ),
    flush=True,
)
entries = []
started = time.monotonic()


def save_report(complete=False):
    report = {
        "stage": 2,
        "question_version": VERSION,
        "model": client.identity,
        "source_total": len(rows),
        "targets": len(selected),
        "out_of_mvp": len(outside),
        "processed": len(entries),
        "counts": dict(Counter(e["status"] for e in entries)),
        "complete": complete,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "ai_review_calls": 0,
        "entries": entries,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    private_json(ROOT / "stage2-model-report.json", report)
    private_json(ROOT / "gpu-metrics.json", client0.metrics)
    print(
        json.dumps({k: v for k, v in report.items() if k not in {"entries", "model"}}), flush=True
    )


def call(requests):
    result = []
    for i in range(0, len(requests), 8):
        group = requests[i : i + 8]
        try:
            result.extend(client.decide(group))
        except Exception:
            if len(group) == 1:
                raise
            result.extend(call(group[: len(group) // 2]))
            result.extend(call(group[len(group) // 2 :]))
    return result


for first in range(0, len(selected), args.batch_size):
    if time.time() >= DEADLINE:
        break
    batch = selected[first : first + args.batch_size]
    requests = []
    works = []
    outputs = {}
    for row in batch:
        key = row["external_id"]
        path = ROOT / "model-results" / (key + ".json")
        if path.exists():
            saved = json.loads(path.read_text())
            if (
                saved.get("material_sha256") == row["record_sha256"]
                and saved.get("question_version") == VERSION
            ):
                entries.append(
                    {
                        "external_id": key,
                        "status": "analyzed",
                        "artifact": str(path),
                        "replayed": True,
                    }
                )
                continue
        text = project_youthcenter_source(row["fields"])
        fields = _fields(text)
        parts = []
        mapping = []
        cursor = 0
        for name in (
            "plcyNm",
            "plcyExplnCn",
            "plcySprtCn",
            "addAplyQlfcCndCn",
            "ptcpPrpTrgtCn",
            "earnEtcCn",
            "etcMttrCn",
            "plcyAplyMthdCn",
            "srngMthdCn",
        ):
            if name not in fields:
                continue
            field = fields[name]
            prefix = "[" + name + "]\n"
            parts.extend((prefix, field.text, "\n\n"))
            lo = cursor + len(prefix)
            mapping.append((lo, lo + len(field.text), field.start, name))
            cursor = lo + len(field.text) + 2
        body = "".join(parts)
        outputs[key] = {
            "format_version": 1,
            "question_version": VERSION,
            "model": client.identity,
            "external_id": key,
            "material_sha256": row["record_sha256"],
            "source_sha256": __import__("hashlib").sha256(text.encode()).hexdigest(),
            "source_fields": row["fields"],
            "source_projection": text,
            "source_fetched_at": row["fetched_at"],
            "windows": [],
            "extraction_spans": [],
            "decisions": [],
            "ai_review_calls": 0,
        }
        for start, end in text_windows(body, 1600):
            request = {
                "state": body[start:end],
                "questions": {
                    kind: {"type": "locate", "instructions": label}
                    for kind, label in {
                        "amount": "payment amount / 지원 금액",
                        "period": "payment duration and frequency / 지원기간·지급주기",
                        "eligibility": "eligibility requirements / 지원대상 자격조건",
                        "application": "application period / 신청·모집 일정",
                        "overlap": "restrictions on receiving other benefits / 중복수혜·기수혜 제한",
                        "exceptions": "exceptions and exclusions / 예외 및 제외 대상",
                    }.items()
                },
            }
            requests.append(request)
            works.append((key, start, mapping, request))
    try:
        responses = call(requests)
        followups = []
        for (key, start, mapping, request), response in zip(works, responses, strict=True):
            spans = []
            for kind, span in (
                (kind, span)
                for kind, answer in response["answers"].items()
                for span in answer["spans"]
            ):
                left, right = start + span["start"], start + span["end"]
                for lo, hi, absolute, name in mapping:
                    a, b = max(left, lo), min(right, hi)
                    if a < b:
                        a0 = absolute + a - lo
                        b0 = absolute + b - lo
                        spans.append(
                            {
                                "kind": kind,
                                "start": a0,
                                "end": b0,
                                "field": name,
                                "text": outputs[key]["source_projection"][a0:b0],
                                "score": span["score"],
                            }
                        )
            outputs[key]["extraction_spans"].extend(spans)
            outputs[key]["windows"].append({"request": request, "response": response})
            merged = []
            for span in sorted(spans, key=lambda s: (s["start"], s["end"])):
                if merged and span["start"] <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], span["end"]))
                else:
                    merged.append((span["start"], span["end"]))
            extracted = (
                "\n".join(outputs[key]["source_projection"][a:b] for a, b in merged)
                or "선택된 조건 없음. 원문에 없는 내용은 미기재로 선택한다."
            )
            followups.append(
                {
                    "state": extracted,
                    "questions": {
                        "amount_kind": {
                            "type": "choice",
                            "instructions": "추출된 금액의 의미 / payment amount type",
                            "criteria": {
                                "fixed": "정액",
                                "cap": "상한이 있는 실비",
                                "variable": "변동·차등",
                                "unstated": "현금 금액 미기재",
                            },
                        },
                        "recipient": {
                            "type": "choice",
                            "instructions": "금전 지원의 수령 주체 / recipient",
                            "criteria": {
                                "applicant": "청년 개인",
                                "employer": "기업·사업주",
                                "mixed": "혼합",
                                "unstated": "미기재",
                            },
                        },
                        "payment_period": {
                            "type": "choice",
                            "instructions": "명시된 지급 주기 / payment frequency",
                            "criteria": {
                                "once": "일회",
                                "monthly": "매월",
                                "yearly": "매년",
                                "other": "기타 주기",
                                "unstated": "미기재",
                            },
                        },
                        "overlap": {
                            "type": "choice",
                            "instructions": "다른 지원금·기수혜 제한 관계 / relationship to other benefits",
                            "criteria": {
                                "restricted": "중복·기수혜 제한 명시",
                                "allowed": "중복 허용 명시",
                                "unstated": "미기재",
                            },
                        },
                        "exception": {
                            "type": "noul",
                            "instructions": "예외 또는 제외 대상 조건이 명시되어 있다 / explicit exception or exclusion",
                        },
                        "amount_variability": {
                            "type": "score",
                            "instructions": "지급액이 달라지는 구조 / payment variability",
                            "criteria": [
                                "정액",
                                "실비에 비례",
                                "소득·자격별 차등",
                                "사업·기관별 별도 산정",
                            ],
                        },
                    },
                }
            )
        decisions = call(followups)
        for (key, _, _, _), response in zip(works, decisions, strict=True):
            outputs[key]["decisions"].append(response)
        for key, value in outputs.items():
            path = ROOT / "model-results" / (key + ".json")
            private_json(path, value)
            entries.append({"external_id": key, "status": "analyzed", "artifact": str(path)})
    except Exception as error:
        code = str(error) if str(error).startswith("decider_") else type(error).__name__
        for key, value in outputs.items():
            path = ROOT / "model-partial" / (key + ".json")
            private_json(path, value | {"error": code})
            entries.append(
                {"external_id": key, "status": "failed", "artifact": str(path), "reason": code}
            )
    save_report()
save_report(len(entries) == len(selected))
