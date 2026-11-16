"""Prepare structured API evidence before inference; preserve every narrative field.

Only official scalar meanings are decoded. Income amounts retain their source unit;
no numeric zero or missing value is interpreted as unrestricted eligibility.
"""

from __future__ import annotations

import re

from benefitfinder.application.decider import LocatedSpan, PreparedSource, SourceField
from benefitfinder.ingestion.youthcenter_codes import (
    YouthcenterCodeError,
    decode_code_list,
)

_ELIGIBILITY = frozenset({
    'jobCd', 'schoolCd', 'earnCndSeCd', 'earnMinAmt', 'earnMaxAmt', 'mrgSttsCd',
    'sbizCd', 'plcyMajorCd', 'sprtTrgtAgeLmtYn', 'sprtTrgtMinAge', 'sprtTrgtMaxAge', 'zipCd',
})
_CODES = frozenset({'jobCd', 'schoolCd', 'earnCndSeCd', 'mrgSttsCd', 'sbizCd', 'plcyPvsnMthdCd', 'plcyMajorCd'})
_METADATA = frozenset({
    'plcyNo', 'plcyNm', 'lclsfNm', 'mclsfNm', 'aplyUrlAddr', 'refUrlAddr1',
    'refUrlAddr2', 'rgtrInstCdNm', 'sprvsnInstCdNm',
})
_SCALARS = _ELIGIBILITY | _CODES | frozenset({
    'aplyPrdSeCd', 'bizPrdSeCd', 'bizPrdBgngYmd', 'bizPrdEndYmd',
    'sprtArvlSeqYn', 'sprtSclCnt', 'sprtSclLmtYn',
})
_HEADER = re.compile(r'(?m)^\[([A-Za-z][A-Za-z0-9]*)\]\r?\n')
_DATE_RANGE = re.compile(r'\s*20\d{6}\s*[~∼～–—-]\s*20\d{6}\s*')


def prepare_source_fields(text: str) -> PreparedSource:
    """Route all fields, including unknown/new fields, without keyword filtering.

    Malformed projections remain complete narrative inputs with an explicit issue.
    Duplicate field headers never overwrite an earlier value.
    """
    headers = list(_HEADER.finditer(text))
    if not headers:
        return PreparedSource((), {}, (SourceField('document', 0, len(text), text),) if text else ())
    spans = []
    structured = {}
    body = []
    issues = []
    if text[:headers[0].start()].strip():
        body.append(SourceField('preamble', 0, headers[0].start(), text[:headers[0].start()]))
        issues.append('source_projection_preamble')
    for index, header in enumerate(headers):
        name = header.group(1)
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        raw = text[header.end():end]
        value = raw.strip()
        start = header.end() + len(raw) - len(raw.lstrip())
        stop = start + len(value)
        if name in structured or any(field.name == name for field in body):
            issues.append('source_field_duplicated')
            # A duplicate is kept for review; it cannot alter a scalar's meaning.
            body.append(SourceField(name, start, stop, value))
            continue
        scalar = name in _SCALARS or name in _METADATA
        if name == 'aplyYmd' and _DATE_RANGE.fullmatch(value):
            scalar = True
        if scalar:
            item = {'raw': value, 'state': 'missing' if not value else 'source_value',
                    'start': start, 'end': stop}
            if name in _CODES and value:
                try:
                    decoded = decode_code_list(name, value)
                    item.update(state=decoded.state.value,
                                meanings=[code.label for code in decoded.codes],
                                values=[code.normalized for code in decoded.codes])
                    if name == 'plcyMajorCd' and decoded.state.value not in {'unrestricted', 'missing'}:
                        issues.append('source_major_requirement_requires_review')
                except YouthcenterCodeError:
                    item['state'] = 'unknown'
                    issues.append('source_code_unknown:' + name)
            if name in {'sprtTrgtAgeLmtYn', 'sprtArvlSeqYn', 'sprtSclLmtYn'} and value:
                if value not in {'Y', 'N'}:
                    item['state'] = 'unknown'
                    issues.append('source_flag_invalid:' + name)
            if name == 'zipCd' and value:
                codes = [code.strip() for code in value.split(',')]
                if all(re.fullmatch(r'[0-9]{5}', code) for code in codes):
                    item.update(state='region_codes', values=list(dict.fromkeys(codes)))
                else:
                    item['state'] = 'unknown'
                    issues.append('source_region_invalid')
            if name in {'earnMinAmt', 'earnMaxAmt', 'sprtTrgtMinAge', 'sprtTrgtMaxAge'} and value:
                if re.fullmatch(r'[0-9]+', value):
                    item['value'] = int(value)
                else:
                    item['state'] = 'unknown'
                    issues.append('source_number_invalid:' + name)
            structured[name] = item
            if value and (name in _ELIGIBILITY or name == 'aplyYmd'):
                kind = 'application' if name == 'aplyYmd' else 'eligibility'
                spans.append(LocatedSpan(kind, start, stop, value, 1.0, 'source_field'))
        elif value:
            body.append(SourceField(name, start, stop, value))
            # Official field identity supplies the candidate's type, never an
            # executable predicate. Even an empty model selection must retain
            # every household/income proviso in these explicitly typed fields.
            if name in {'earnEtcCn', 'addAplyQlfcCndCn', 'ptcpPrpTrgtCn'}:
                kind = 'exceptions' if name == 'ptcpPrpTrgtCn' else 'eligibility'
                spans.append(LocatedSpan(kind, start, stop, value, 1.0, 'source_field'))
        # The support body is evidence for deterministic parsers, never a fixed
        # amount assertion. Keeping the whole field prevents a cap/deduction from
        # disappearing when a model selects only a number.
        if name == 'plcySprtCn' and value:
            spans.extend(LocatedSpan(kind, start, stop, value, 1.0, 'source_field')
                         for kind in ('amount', 'duration', 'timing'))
    return PreparedSource(tuple(spans), structured, tuple(body), tuple(dict.fromkeys(issues)))
