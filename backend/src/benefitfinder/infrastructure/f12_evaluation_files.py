"""Read local F12 v1 artifacts for evaluation; no dependency on unmerged extractor code."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NoReturn

from benefitfinder.application.f12_evaluation import EvaluationCase, EvaluationItem, evaluate_cases
from benefitfinder.application.policy_rules import (
    ReviewOperation, RuleCandidate, RuleCitation, RuleDirection, RuleKind, SourceDocument,
)
from benefitfinder.infrastructure.policy_rule_files import (
    RuleReviewFileError, load_rule_snapshot, parse_rule_snapshot,
)


class EvaluationFileError(ValueError):
    """Fixed code only; never include source text, paths, or reviewer data."""


def _invalid() -> NoReturn:
    raise EvaluationFileError("invalid_f12_evaluation_input")


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        _invalid()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid()
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 100:
        _invalid()
    return tuple(_text(x) for x in value)


def _time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value))
        if parsed.utcoffset() is None:
            _invalid()
        return parsed
    except ValueError:
        _invalid()


def _hash(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _invalid()
    return value


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    _invalid()


def _read_json(
    path: Path, max_bytes: int, versions: tuple[int, ...] = (1,),
    *, limit_error: bool = False,
) -> tuple[dict[str, object], int]:
    try:
        with path.open('rb') as file:
            raw = file.read(max_bytes + 1)
        if len(raw) > max_bytes:
            if limit_error:
                _budget_exceeded()
            _invalid()
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
        if (not isinstance(data, dict) or type(data.get('format_version')) is not int
                or data['format_version'] not in versions):
            _invalid()
        return data, len(raw)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _invalid()


def _read(path: Path) -> dict[str, object]:
    return _read_json(path, 32 * 1024 * 1024)[0]


def _citation(raw: object) -> RuleCitation:
    c = _object(raw, {"document_id", "document_version", "content_sha256", "quote", "location"})
    return RuleCitation(_text(c["document_id"]), _text(c["document_version"]),
                        _hash(c["content_sha256"]), _text(c["quote"]), _text(c["location"]))


def _source(raw: object, document: SourceDocument) -> None:
    s = _object(raw, {"id", "url", "document_version", "content_sha256", "collected_at"})
    if (s["id"], s["url"], s["document_version"], _hash(s["content_sha256"])) != (
        document.id, document.source_url, document.document_version, document.content_sha256
    ) or _time(s["collected_at"]) != document.collected_at:
        _invalid()


def _rule(raw: object, item_id: str, document: SourceDocument) -> EvaluationItem:
    r = _object(raw, {"id", "source_policy_id", "target_policy_ids", "kind", "description",
                      "citations", "direction", "conditions", "exceptions", "period",
                      "amount_effect", "unresolved"})
    _text(r["id"])
    _text(r["description"])
    if r["source_policy_id"] != document.policy_id:
        _invalid()
    targets = _strings(r["target_policy_ids"])
    if not targets or len(targets) != len(set(targets)):
        _invalid()
    kind, direction = RuleKind(r["kind"]), RuleDirection(r["direction"])
    _strings(r["conditions"])
    _strings(r["unresolved"])
    for name in ("period", "amount_effect"):
        if r[name] is not None:
            _text(r[name])
    if not isinstance(r["citations"], list):
        _invalid()
    return EvaluationItem(item_id, "rule", tuple(_citation(c) for c in r["citations"]),
                          targets, kind.value, direction.value, _strings(r["exceptions"]))


def _from_rule(candidate: RuleCandidate, index: int, document: SourceDocument) -> EvaluationItem:
    if candidate.source_policy_id != document.policy_id:
        _invalid()
    return EvaluationItem(f"rule:{index}", "rule", candidate.citations,
                          candidate.target_policy_ids, candidate.kind.value,
                          candidate.direction.value, candidate.exceptions)


def _fact(raw: object, item_id: str, kind: str) -> EvaluationItem:
    item = _object(raw, {"evidence", "amounts_won"} if kind == "amount" else {"evidence"})
    amounts: tuple[int, ...] = ()
    if kind == "amount":
        values = item["amounts_won"]
        if (not isinstance(values, list) or len(values) > 100
                or any(type(x) is not int or x < 0 for x in values)):
            _invalid()
        amounts = tuple(sorted(values))
    return EvaluationItem(item_id, kind, (_citation(item["evidence"]),), amounts_won=amounts)


def _document(
    directory: Path, category: str, read: Callable[[Path], dict[str, object]] | None = None,
) -> tuple[SourceDocument, tuple[EvaluationItem, ...]]:
    path = directory / "rules-pending.json"
    snapshot = load_rule_snapshot(path) if read is None else parse_rule_snapshot(read(path))
    if snapshot.operation != ReviewOperation.PREPARED or len(snapshot.batch.documents) != 1:
        _invalid()
    (document,) = snapshot.batch.documents
    if document.document_version.startswith("sha256:") and (
        document.document_version != "sha256:" + document.content_sha256
    ):
        _invalid()
    policies = [p for p in snapshot.batch.policies if p.id == document.policy_id]
    if len(policies) != 1 or policies[0].domain != "youth" or category not in policies[0].categories:
        _invalid()
    if tuple(r.candidate for r in snapshot.reviews) != snapshot.batch.candidates:
        _invalid()
    if any(r.approval is not None for r in snapshot.reviews):
        _invalid()
    items = tuple(_from_rule(c, i, document) for i, c in enumerate(snapshot.batch.candidates, 1))
    return document, items


def _candidate_facts(directory: Path, document: SourceDocument) -> tuple[EvaluationItem, ...]:
    return _facts_data(_read(directory / "facts-pending.json"), document)


def _facts_data(
    data: dict[str, object], document: SourceDocument, max_items: int = 100,
) -> tuple[EvaluationItem, ...]:
    d = _object(data, {
        "format_version", "policy_id", "source", "status", "eligibility", "benefit_amount",
        "payment_timing",
    })
    if (type(d["format_version"]) is not int or d["format_version"] != 1
            or d["policy_id"] != document.policy_id or d["status"] != "needs_review"):
        _invalid()
    _source(d["source"], document)
    result = []
    for name, kind in (("eligibility", "eligibility"), ("benefit_amount", "amount"),
                       ("payment_timing", "timing")):
        keys = {"status", "items", "scheduled_monthly_day"} if kind == "timing" else {"status", "items"}
        section = _object(d[name], keys)
        raw_items = section["items"]
        if (not isinstance(raw_items, list) or len(raw_items) > max_items
                or section["status"] != ("extracted" if raw_items else "not_found")):
            _invalid()
        if kind == "timing":
            day = section["scheduled_monthly_day"]
            if day is not None and (type(day) is not int or not 1 <= day <= 31 or not raw_items):
                _invalid()
        result.extend(_fact(x, f"{kind}:{i}", kind) for i, x in enumerate(raw_items, 1))
    return tuple(result)


def _approved(
    path: Path, document: SourceDocument, read: Callable[[Path], dict[str, object]] = _read,
) -> tuple[EvaluationItem, ...]:
    d = _object(read(path), {"format_version", "kind", "pair_sha256", "policy_id", "source", "items"})
    if d["kind"] != "f12_approved_review_export" or d["policy_id"] != document.policy_id:
        _invalid()
    _hash(d["pair_sha256"])
    _source(d["source"], document)
    if not isinstance(d["items"], list) or not 1 <= len(d["items"]) <= 100:
        _invalid()
    result = []
    for raw in d["items"]:
        item = _object(raw, {"item_id", "kind", "approval", "candidate"})
        item_id, kind = _text(item["item_id"]), item["kind"]
        if kind not in {"rule", "eligibility", "amount", "timing"} or not re.fullmatch(
            re.escape(kind) + r":[1-9][0-9]*", item_id
        ):
            _invalid()
        approval = _object(item["approval"], {"reviewer", "reason", "reviewed_at"})
        _text(approval["reviewer"])
        _text(approval["reason"])
        if _time(approval["reviewed_at"]) < document.collected_at:
            _invalid()
        result.append(_rule(item["candidate"], item_id, document) if kind == "rule"
                      else _fact(item["candidate"], item_id, kind))
    return tuple(result)


def load_evaluation_cases(manifest: Path) -> tuple[EvaluationCase, ...]:
    """Read v1 case manifest and existing F12 pairs/approved exports without writing."""
    try:
        d = _object(_read(manifest), {"format_version", "cases"})
        if not isinstance(d["cases"], list) or not 1 <= len(d["cases"]) <= 100:
            _invalid()
        result = []
        for raw in d["cases"]:
            keys = {"id", "category", "reference_dir", "approved_exports", "candidate_dir"}
            if not isinstance(raw, dict) or set(raw) not in (keys, keys | {"candidate_error"}):
                _invalid()
            c = raw
            category = c["category"]
            if category not in {"employment", "housing"}:
                _invalid()
            reference_dir = (manifest.parent / _text(c["reference_dir"])).resolve()
            reference, _ = _document(reference_dir, category)
            if not reference.official:
                _invalid()
            error = c.get("candidate_error")
            if error is not None:
                if c["candidate_dir"] is not None or error not in {
                    "context_limit", "model_failure", "extraction_failed"
                }:
                    _invalid()
                candidate, candidates = None, ()
            else:
                candidate_dir = (manifest.parent / _text(c["candidate_dir"])).resolve()
                if reference_dir == candidate_dir:
                    _invalid()
                candidate, rules = _document(candidate_dir, category)
                candidates = rules + _candidate_facts(candidate_dir, candidate)
            exports = _strings(c["approved_exports"])
            if not exports:
                _invalid()
            approved = tuple(i for path in exports for i in _approved(
                (manifest.parent / path).resolve(), reference
            ))
            result.append(EvaluationCase(_text(c["id"]), category, reference, candidate,
                                         approved, candidates, error))
        return tuple(result)
    except (RuleReviewFileError, ValueError, TypeError, KeyError, RecursionError):
        _invalid()


def evaluate_manifest(manifest: Path, *, view: str | None = None) -> dict[str, object]:
    """Build a local report without revealing original content through exceptions."""
    try:
        data, size = _read_json(manifest, 32 * 1024 * 1024, (1, 2))
        if data['format_version'] == 2:
            return _evaluate_large(manifest, data, size, view or 'both')
        if view is not None:
            _invalid()
        return evaluate_cases(load_evaluation_cases(manifest))
    except EvaluationFileError:
        raise
    except (RuleReviewFileError, ValueError, TypeError, KeyError, RecursionError):
        _invalid()


def write_evaluation_report(path: Path, report: dict[str, object]) -> None:
    """Create only; a failed write never replaces any prior report or review artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".f12-evaluation-", delete=False) as f:
            temporary = Path(f.name)
            f.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# v2 opts into a bounded whole-run reader. Legacy v1's 100-item semantics stay intact.
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_FILES = 64
_MAX_SECTION_ITEMS = 2000
_MAX_CANDIDATES = 5000
_MAX_CITATIONS = 5000
_MAX_REFERENCE_ITEMS = 200
_MAX_WORK = 1_000_000
_MAX_LINE_WORK = 10_000_000
_MAX_SOURCE_CHARACTERS = 200_000
_MAX_SOURCE_LINES = 10_000
_MAX_QUOTE_CHARACTERS = 4_000_000
_KINDS = ('eligibility', 'amount', 'timing', 'exceptions')


def _budget_exceeded() -> NoReturn:
    raise EvaluationFileError('f12_evaluation_budget_exceeded')


def _limit(condition: bool) -> None:
    if not condition:
        _budget_exceeded()


@dataclass
class _Budget:
    files: dict[Path, dict[str, object]] = field(default_factory=dict)
    total_bytes: int = 0
    candidates: int = 0
    citations: int = 0
    references: int = 0
    quote_characters: int = 0
    work: int = 0
    line_work: int = 0

    def read(self, path: Path) -> dict[str, object]:
        path = path.resolve()
        if path not in self.files:
            _limit(len(self.files) < _MAX_FILES)
            remaining = _MAX_TOTAL_BYTES - self.total_bytes
            _limit(remaining >= 0)
            data, size = _read_json(path, min(_MAX_FILE_BYTES, remaining), limit_error=True)
            self.files[path] = data
            self.total_bytes += size
        return self.files[path]

    def document(self, document: SourceDocument) -> None:
        _limit(len(document.text) <= _MAX_SOURCE_CHARACTERS)
        _limit(len(document.text.splitlines()) <= _MAX_SOURCE_LINES)
        _limit(all(len(x) <= 4096 for x in (document.id, document.policy_id,
                                           document.source_url, document.document_version)))

    def items(self, items: tuple[EvaluationItem, ...], *, reference: bool = False) -> None:
        if reference:
            self.references += len(items)
            _limit(self.references <= _MAX_REFERENCE_ITEMS)
        else:
            self.candidates += len(items)
            _limit(self.candidates <= _MAX_CANDIDATES)
        for item in items:
            _limit(len(item.id) <= 256)
            self.citations += len(item.citations)
            _limit(self.citations <= _MAX_CITATIONS)
            for citation in item.citations:
                _limit(len(citation.quote) <= 64_000 and len(citation.location) <= 4096)
                self.quote_characters += len(citation.quote)
                _limit(self.quote_characters <= _MAX_QUOTE_CHARACTERS)

    def evaluation(self, case: EvaluationCase, extra: tuple[EvaluationItem, ...]) -> None:
        citations = sum(len(i.citations) for i in (*case.candidates, *extra))
        self.work += sum(len(i.citations) for i in case.references) * citations
        self.line_work += len((case.candidate_document or case.reference_document).text.splitlines()) * citations
        _limit(self.work <= _MAX_WORK and self.line_work <= _MAX_LINE_WORK)

    def summary(self) -> dict[str, object]:
        return {'files_read': len(self.files), 'bytes_read': self.total_bytes,
                'candidate_items_validated': self.candidates, 'reference_items': self.references,
                'citations_validated': self.citations, 'quote_characters': self.quote_characters,
                'reserved_reference_citation_checks': self.work, 'reserved_source_line_checks': self.line_work,
                'limits': {'data_file_bytes': _MAX_FILE_BYTES, 'manifest_bytes': 32 * 1024 * 1024,
                           'total_bytes': _MAX_TOTAL_BYTES, 'files': _MAX_FILES,
                           'section_items': _MAX_SECTION_ITEMS, 'candidate_items': _MAX_CANDIDATES,
                           'citations': _MAX_CITATIONS, 'reference_items': _MAX_REFERENCE_ITEMS,
                           'reference_citation_checks': _MAX_WORK, 'source_line_checks': _MAX_LINE_WORK,
                           'source_characters': _MAX_SOURCE_CHARACTERS, 'source_lines': _MAX_SOURCE_LINES,
                           'quote_characters': _MAX_QUOTE_CHARACTERS, 'single_quote_characters': 64_000}}


def _large_facts(data: object, document: SourceDocument, budget: _Budget) -> tuple[EvaluationItem, ...]:
    if not isinstance(data, dict):
        _invalid()
    for name in ('eligibility', 'benefit_amount', 'payment_timing'):
        section = data.get(name)
        if not isinstance(section, dict) or not isinstance(section.get('items'), list):
            _invalid()
        _limit(len(section['items']) <= _MAX_SECTION_ITEMS)
    result = _facts_data(data, document, _MAX_SECTION_ITEMS)
    budget.items(result)
    return result


def _large_path(parent: Path, raw: object) -> Path:
    value = _text(raw)
    _limit(len(value) <= 4096)
    return (parent / value).resolve()


def _range_map(raw: object, start: int, end: int) -> dict[str, set[int]]:
    data = _object(raw, set(_KINDS))
    result = {}
    for kind, ranges in data.items():
        if not isinstance(ranges, list) or len(ranges) > 220:
            _invalid()
        selected: set[int] = set()
        previous = start - 2
        for span in ranges:
            if (not isinstance(span, list) or len(span) != 2
                    or any(type(n) is not int for n in span)
                    or not start <= span[0] <= span[1] <= end or span[0] <= previous + 1):
                _invalid()
            selected.update(range(span[0], span[1] + 1))
            previous = span[1]
        result[kind] = selected
    return result


def _exception_items(raw: object, budget: _Budget) -> tuple[EvaluationItem, ...]:
    if not isinstance(raw, list):
        _invalid()
    _limit(len(raw) <= _MAX_SECTION_ITEMS)
    result = tuple(_fact(item, f'exception:{i}', 'eligibility') for i, item in enumerate(raw, 1))
    budget.items(result)
    return result


def _chunk_views(
    directory: Path, document: SourceDocument, budget: _Budget,
) -> tuple[dict[str, tuple[EvaluationItem, ...]], dict[str, tuple[EvaluationItem, ...]]]:
    completion = _object(budget.read(directory / 'completion.json'),
                         {'format_version', 'state', 'status', 'approval_effect'})
    if (completion['state'], completion['status'], completion['approval_effect']) != (
        'complete', 'needs_review', 'none'
    ):
        _invalid()
    d = _object(budget.read(directory / 'chunked-evidence.json'), {
        'format_version', 'kind', 'source', 'model', 'think', 'num_ctx', 'num_predict',
        'response_format', 'aggregation', 'extraction_complete', 'source_lines', 'chunks',
        'exceptions', 'context_groups', 'model_only_facts', 'model_only_exceptions',
        'requires_human_review', 'approval_effect', 'limitations',
    })
    if (d['kind'] != 'f12_chunked_evidence' or d['extraction_complete'] is not True
            or d['requires_human_review'] is not True or d['approval_effect'] != 'none'
            or type(d['think']) is not bool):
        _invalid()
    for key in ('model', 'response_format', 'aggregation'):
        _text(d[key])
    for key in ('num_ctx', 'num_predict'):
        if type(d[key]) is not int or d[key] < 1:
            _invalid()
    _strings(d['limitations'])
    _source(d['source'], document)
    lines = document.text.splitlines(keepends=True)
    if type(d['source_lines']) is not int or d['source_lines'] != len(lines):
        _invalid()
    if not isinstance(d['context_groups'], list):
        _invalid()
    _limit(len(d['context_groups']) <= _MAX_SECTION_ITEMS)
    for raw in d['context_groups']:
        group = _object(raw, {'kind', 'start', 'end'})
        if (group['kind'] not in {'faq', 'footnote'}
                or type(group['start']) is not int or type(group['end']) is not int
                or not 1 <= group['start'] <= group['end'] <= len(lines)):
            _invalid()
    if not isinstance(d['chunks'], list) or not d['chunks']:
        _invalid()
    _limit(len(d['chunks']) <= 500)
    totals = {view: {k: set() for k in _KINDS} for view in ('model', 'context')}
    core_end = 0
    for index, raw in enumerate(d['chunks'], 1):
        c = _object(raw, {'index', 'core_start', 'core_end', 'start', 'end', 'selected',
                          'contextualized', 'context_added', 'model_called', 'attempts',
                          'repair_reason', 'content_sha256'})
        if (any(type(c[k]) is not int for k in ('index', 'core_start', 'core_end', 'start', 'end', 'attempts'))
                or c['index'] != index or c['core_start'] != core_end + 1
                or not 1 <= c['start'] <= c['core_start'] <= c['core_end'] <= c['end'] <= len(lines)
                or c['end'] - c['start'] + 1 > 220
                or len(''.join(lines[c['start']-1:c['end']])) > 6000
                or c['content_sha256'] != document.content_sha256
                or type(c['model_called']) is not bool
                or c['attempts'] not in ((1, 2) if c['model_called'] else (0,))
                or c['repair_reason'] != ('chunk_response_invalid' if c['attempts'] == 2 else None)):
            _invalid()
        core_end = c['core_end']
        model = _range_map(c['selected'], c['start'], c['end'])
        context = _range_map(c['contextualized'], c['start'], c['end'])
        added = _range_map(c['context_added'], c['start'], c['end'])
        for kind in _KINDS:
            if (any(not lines[n-1].strip() for n in model[kind])
                    or not model[kind] <= context[kind]
                    or added[kind] != context[kind] - model[kind]
                    or (not c['model_called'] and (model[kind] or context[kind]))):
                _invalid()
            totals['model'][kind].update(model[kind])
            totals['context'][kind].update(context[kind])
    if core_end != len(lines):
        _invalid()
    facts = {'model': _large_facts(d['model_only_facts'], document, budget),
             'context': _large_facts(budget.read(directory / 'facts-pending.json'), document, budget)}
    extras = {'model': _exception_items(d['model_only_exceptions'], budget),
              'context': _exception_items(d['exceptions'], budget)}
    # Match ALL materialized line selections to their trace. No truncation or raw/context substitution.
    for view in ('model', 'context'):
        for kind in _KINDS:
            items = extras[view] if kind == 'exceptions' else tuple(i for i in facts[view] if i.kind == kind)
            actual: set[int] = set()
            for item in items:
                citation = item.citations[0]
                match = re.fullmatch(r'text lines ([1-9][0-9]*)-([1-9][0-9]*)', citation.location)
                if match is None:
                    _invalid()
                a, b = int(match[1]), int(match[2])
                if not 1 <= a <= b <= len(lines):
                    _invalid()
                actual.update(n for n in range(a, b+1) if lines[n-1].strip())
            if actual != {n for n in totals[view][kind] if lines[n-1].strip()}:
                _invalid()
    # Context may restore words, but cannot invent or replace numeric model values.
    raw_amounts = tuple(i for i in facts['model'] if i.kind == 'amount')
    for item in (i for i in facts['context'] if i.kind == 'amount'):
        a, b = map(int, re.search(r'([0-9]+)-([0-9]+)$', item.citations[0].location).groups())
        expected = set()
        for original in raw_amounts:
            left, right = map(int, re.search(r'([0-9]+)-([0-9]+)$', original.citations[0].location).groups())
            if left <= b and right >= a:
                expected.update(original.amounts_won)
        if set(item.amounts_won) != expected:
            _invalid()
    if (d['model_only_facts']['payment_timing']['scheduled_monthly_day']
            != budget.read(directory / 'facts-pending.json')['payment_timing']['scheduled_monthly_day']):
        _invalid()
    return facts, extras


def _evaluate_large(
    manifest: Path, data: dict[str, object], size: int, view: str,
) -> dict[str, object]:
    if view not in {'model', 'context', 'both'}:
        _invalid()
    _object(data, {'format_version', 'cases'})
    if not isinstance(data['cases'], list) or not data['cases']:
        _invalid()
    _limit(len(data['cases']) <= 20 and size <= _MAX_TOTAL_BYTES and _MAX_FILES >= 1)
    budget = _Budget(files={manifest.resolve(): data}, total_bytes=size)
    views: dict[str, list[EvaluationCase]] = {'model': [], 'context': []}
    auxiliary: dict[str, list[tuple[EvaluationItem, ...]]] = {'model': [], 'context': []}
    formats = []
    for raw in data['cases']:
        required = {'id', 'category', 'reference_dir', 'approved_exports', 'candidate_dir'}
        if (not isinstance(raw, dict) or not required <= set(raw)
                or set(raw) - required - {'candidate_format', 'candidate_error'}):
            _invalid()
        case_id = _text(raw['id'])
        _limit(len(case_id) <= 128)
        category = raw['category']
        if category not in {'employment', 'housing'}:
            _invalid()
        reference_dir = _large_path(manifest.parent, raw['reference_dir'])
        reference, _ = _document(reference_dir, category, budget.read)
        budget.document(reference)
        if not reference.official:
            _invalid()
        exports = _strings(raw['approved_exports'])
        if not exports:
            _invalid()
        approved = tuple(i for path in exports for i in _approved(
            _large_path(manifest.parent, path), reference, budget.read
        ))
        budget.items(approved, reference=True)
        candidate_format = raw.get('candidate_format', 'facts-v1')
        if candidate_format not in {'facts-v1', 'chunked-v1'}:
            _invalid()
        error = raw.get('candidate_error')
        if error is not None:
            if raw['candidate_dir'] is not None or error not in {
                'context_limit', 'model_failure', 'extraction_failed'
            }:
                _invalid()
            document, rules = None, ()
            facts = {'model': (), 'context': ()}
            extras = {'model': (), 'context': ()}
        else:
            directory = _large_path(manifest.parent, raw['candidate_dir'])
            if directory == reference_dir:
                _invalid()
            document, rules = _document(directory, category, budget.read)
            budget.document(document)
            budget.items(rules)
            if candidate_format == 'chunked-v1':
                facts, extras = _chunk_views(directory, document, budget)
            else:
                provided = _large_facts(budget.read(directory / 'facts-pending.json'), document, budget)
                facts = {'model': provided, 'context': provided}
                extras = {'model': (), 'context': ()}
        formats.append({'id': case_id, 'candidate_format': candidate_format,
                        'view_origin': 'unavailable' if error is not None else (
                            'provided_shared' if candidate_format == 'facts-v1' else 'recorded_separate')})
        for key in views:
            case = EvaluationCase(case_id, category, reference, document, approved, rules + facts[key], error)
            budget.evaluation(case, extras[key])
            views[key].append(case)
            auxiliary[key].append(extras[key])
    results = {}
    requested = ('model', 'context') if view == 'both' else (view,)
    for key in requested:
        cases = tuple(views[key])
        report = evaluate_cases(cases)
        count = sum(len(c.candidates) for c in cases)
        citations = sum(len(i.citations) for c in cases for i in c.candidates)
        reported_count = sum(len(c['candidates']) for c in report['cases'])
        reported_citations = report['summary']['candidate_citations']
        if (count, citations) != (reported_count, reported_citations):
            raise EvaluationFileError('f12_evaluation_accounting_mismatch')
        # Ground auxiliary exception citations separately. They do not become
        # eligibility candidates, approvals, or a new denominator for the 20-item score.
        extra_cases = tuple(replace(c, candidates=e) for c, e in zip(cases, auxiliary[key], strict=True))
        extra_report = evaluate_cases(extra_cases)
        extra_entries = [{'id': c['id'], 'candidates': c['candidates']} for c in extra_report['cases']]
        extra_count = sum(len(e) for e in auxiliary[key])
        extra_citations = sum(len(i.citations) for e in auxiliary[key] for i in e)
        es = extra_report['summary']
        if (extra_count != sum(len(c['candidates']) for c in extra_report['cases'])
                or extra_citations != es['candidate_citations']):
            raise EvaluationFileError('f12_evaluation_accounting_mismatch')
        if es['citation_errors']:
            report['assessment'] = 'findings'
        report.update({
            'view': key, 'input_formats': formats,
            'input_accounting': {'candidate_items_read': count, 'candidate_items_evaluated': reported_count,
                                 'citations_read': citations, 'citations_evaluated': reported_citations},
            'auxiliary_exceptions': {'items_read': extra_count, 'items_evaluated': extra_count,
                                     'citations_read': extra_citations, 'citations_evaluated': es['candidate_citations'],
                                     'grounded_citations': es['grounded_candidate_citations'],
                                     'citation_errors': es['citation_errors'], 'cases': extra_entries},
            'resource_usage': budget.summary(),
        })
        report['limitations'].extend([
            '일반 facts-v1 입력은 두 뷰에 동일한 제공 자료를 사용합니다. 분리된 모델 기록이라고 추정하지 않습니다.',
            'chunked-v1 기록의 형식·줄 선택·자료 간 정합성을 검사하지만 모델 실행이나 문맥 해석의 진위를 인증하지 않습니다.',
            '별도 예외 인용은 원문 일치만 검사합니다. 해당 인용의 의미적 완전성이나 승인을 뜻하지 않습니다.',
        ])
        results[key] = report
    if view != 'both':
        return results[view]
    return {'format_version': 2, 'kind': 'f12_extraction_comparison',
            'assessment': 'findings' if any(r['assessment'] == 'findings' for r in results.values()) else 'no_findings',
            'scope': 'approved_reference_only', 'requires_human_review': True, 'approval_effect': 'none',
            'views': results, 'resource_usage': budget.summary(),
            'limitations': ['두 뷰는 같은 승인 기준을 사용하며 문맥 보존 결과를 모델 단독 정확도로 해석하지 않습니다.']}
