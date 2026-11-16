"""Structured API preparation first; one bounded narrative locate pass only."""

from __future__ import annotations

from dataclasses import asdict
from collections.abc import Callable

from benefitfinder.application.decider import (
    DeciderError, Decisions, DocumentExtraction, LocatedSpan, PreparedSource, SourceField,
)
from benefitfinder.application.policy_analysis import PolicyAnalysisInput

LOCATE_QUESTIONS = {
    'eligibility': 'eligibility requirements / 지원대상 자격조건',
    'exceptions': 'exceptions / 예외 및 제외 대상',
    'overlap': 'All clauses on other benefits or previous participation, including allowance, prohibition, deduction and timing. 중복수혜·기수혜·차감·기간 조건 전체. 미기재를 허용으로 추정하지 않는다.',
}


def validated_locate_spans(text: str, spans: list[LocatedSpan]) -> list[LocatedSpan]:
    """Validate source coordinates and retain all three independent evidence kinds."""
    merged = []
    for span in sorted(set(spans), key=lambda item: (item.kind, item.start, item.end)):
        if (span.kind not in LOCATE_QUESTIONS or span.method != 'locate'
                or not 0 <= span.start < span.end <= len(text)
                or text[span.start:span.end] != span.text):
            raise DeciderError('decider_evidence_mismatch')
        if merged and merged[-1].kind == span.kind and span.start <= merged[-1].end:
            previous = merged.pop()
            end = max(previous.end, span.end)
            merged.append(LocatedSpan(span.kind, previous.start, end,
                                      text[previous.start:end], max(previous.score, span.score)))
        else:
            merged.append(span)
    return merged


def contextualize_spans(text: str, spans: list[LocatedSpan], prepared: PreparedSource) -> list[LocatedSpan]:
    """Keep household names, qualifiers and exceptions around sparse selections.

    Typed narrative fields retain their complete context. Elsewhere retain complete
    source lines. This is deterministic source context, not additional AI recall.
    """
    expanded = []
    for span in spans:
        field = next((field for field in prepared.body
                      if field.start <= span.start < span.end <= field.end), None)
        if field is None:
            raise DeciderError('decider_preparation_source_mismatch')
        if field.name in {'earnEtcCn', 'addAplyQlfcCndCn', 'ptcpPrpTrgtCn'}:
            start, end = field.start, field.end
        else:
            start = max(field.start, text.rfind('\n', field.start, span.start) + 1)
            boundary = text.find('\n', span.end, field.end)
            end = field.end if boundary < 0 else boundary
        expanded.append(LocatedSpan(span.kind, start, end, text[start:end], span.score,
                                    'source_context'))
    # Different windows may select the same clause. Do not multiply its rules.
    return sorted(set(expanded), key=lambda span: (span.kind, span.start, span.end))


def text_windows(text: str, maximum: int) -> list[tuple[int, int]]:
    """Cover every codepoint with overlapping context; never truncate a source."""
    if not 256 <= maximum <= 20000:
        raise ValueError('decider_invalid_window_size')
    windows = []
    start = 0
    while start < len(text):
        end = min(len(text), start + maximum)
        if end < len(text):
            boundary = text.rfind('\n', start + maximum // 2, end)
            if boundary >= 0:
                end = boundary + 1
        windows.append((start, end))
        if end == len(text):
            break
        start = max(start + 1, end - min(256, maximum // 4))
    return windows


def body_projection(prepared: PreparedSource) -> tuple[str, tuple[tuple[int, int, SourceField], ...]]:
    """Build a narrative-only view with a reversible source-coordinate mapping."""
    parts = []
    segments = []
    offset = 0
    for field in prepared.body:
        prefix = f'[{field.name}]\n' if field.name != 'document' else ''
        parts.extend((prefix, field.text, '\n\n'))
        start = offset + len(prefix)
        segments.append((start, start + len(field.text), field))
        offset = start + len(field.text) + 2
    text = ''.join(parts)
    # Preserve the exact original text for unstructured supplemental documents.
    if len(prepared.body) == 1 and prepared.body[0].name == 'document':
        text = prepared.body[0].text
    return text, tuple(segments)


def analyze_batch(values: tuple[PolicyAnalysisInput, ...], client: Decisions, *,
                  batch_size: int = 4, window_chars: int = 1800,
                  prepare_source: Callable[[str], PreparedSource] | None = None,
                  max_policy_requests: int = 32, max_policy_characters: int = 40000,
                  ) -> tuple[dict[str, tuple[DocumentExtraction, ...]], dict[str, str]]:
    if (not 1 <= batch_size <= 32 or len({v.policy_id for v in values}) != len(values)
            or not 1 <= max_policy_requests <= 1000 or not 1 <= max_policy_characters <= 1_000_000):
        raise ValueError('decider_invalid_policy_batch')
    # Validate configuration even when all policies contain only scalar fields.
    text_windows('', window_chars)
    work = []
    plans = {}
    spans: dict[str, dict[str, list[LocatedSpan]]] = {v.policy_id: {} for v in values}
    failures: dict[str, str] = {}
    for value in values:
        pending = []
        characters = 0
        for document in value.documents:
            prepared = (prepare_source(document.text) if prepare_source is not None else
                        PreparedSource((), {}, (SourceField('document', 0, len(document.text), document.text),)))
            for field in prepared.body:
                if (not 0 <= field.start <= field.end <= len(document.text)
                        or document.text[field.start:field.end] != field.text):
                    raise DeciderError('decider_preparation_source_mismatch')
            projected, mapping = body_projection(prepared)
            windows = text_windows(projected, window_chars)
            characters += len(projected)
            plans[(value.policy_id, document.id)] = (prepared, mapping, len(projected), len(windows))
            spans[value.policy_id][document.id] = []
            pending.extend((value.policy_id, document, start, mapping, {
                'state': projected[start:end],
                'questions': {kind: {'type': 'locate', 'instructions': instructions}
                              for kind, instructions in LOCATE_QUESTIONS.items()},
            }) for start, end in windows)
        # Preflight the entire policy before spending even its first model request.
        if len(pending) > max_policy_requests or characters > max_policy_characters:
            failures[value.policy_id] = 'decider_policy_analysis_budget_exceeded'
        elif any(plans[(value.policy_id, doc.id)][0].issues for doc in value.documents):
            failures[value.policy_id] = 'decider_source_preparation_requires_review'
        else:
            work.extend(pending)

    def execute(items: list) -> list[tuple[object, dict]]:
        if not items:
            return []
        try:
            responses = client.decide([item[4] for item in items])
            return list(zip(items, responses, strict=True))
        except DeciderError as error:
            # Only a batch/input-size failure benefits from splitting. A provider
            # outage or exhausted budget must not multiply failed API calls.
            if len(items) > 1 and str(error) == 'decider_input_length_exceeded':
                middle = len(items) // 2
                return execute(items[:middle]) + execute(items[middle:])
            for item in items:
                failures[item[0]] = str(error)
            return []

    for index in range(0, len(work), batch_size):
        active = [item for item in work[index:index + batch_size] if item[0] not in failures]
        for (policy_id, document, start, mapping, request), response in execute(active):
            if set(response['answers']) != set(LOCATE_QUESTIONS):
                raise DeciderError('decider_unexpected_analysis_questions')
            for kind, answer in response['answers'].items():
                for found in answer['spans']:
                    left, right = start + found['start'], start + found['end']
                    if (not 0 <= found['start'] < found['end'] <= len(request['state'])
                            or request['state'][found['start']:found['end']] != found['text']):
                        raise DeciderError('decider_evidence_mismatch')
                    for projected_start, projected_end, field in mapping:
                        begin, end = max(left, projected_start), min(right, projected_end)
                        if begin < end:
                            source_start = field.start + begin - projected_start
                            source_end = field.start + end - projected_start
                            spans[policy_id][document.id].append(LocatedSpan(
                                kind, source_start, source_end, document.text[source_start:source_end], found['score']))
    results = {}
    for value in values:
        if value.policy_id in failures:
            continue
        documents = []
        for document in value.documents:
            prepared, _, body_characters, requests = plans[(value.policy_id, document.id)]
            raw = spans[value.policy_id][document.id]
            located = contextualize_spans(document.text, validated_locate_spans(document.text, raw), prepared)
            audit = {'structured': prepared.structured, 'body_fields': [asdict(field) for field in prepared.body],
                     'issues': list(prepared.issues), 'source_characters': len(document.text),
                     'body_characters': body_characters, 'model_requests': requests,
                     'question_count': len(LOCATE_QUESTIONS), 'followup_requests': 0}
            documents.append(DocumentExtraction(document, tuple((*prepared.spans, *located)),
                {'analysis_mode': 'structured_first'}, tuple(raw), audit))
        results[value.policy_id] = tuple(documents)
    return results, failures
