"""Source-only bulk preparation with injected storage, validation and publication.

Checkpoint state never authorizes skipping a fresh release lookup. A lost checkpoint
after a committed write is recovered from the current published record identity.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput


@dataclass
class _Prepared:
    value: PolicyAnalysisInput
    record: object
    source: str
    entry: dict
    checkpoint: Path
    payload: dict | None
    normalized: dict


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _error(error: Exception, fallback: str) -> dict:
    # Fixed internal/trigger codes may be reported; DSNs and driver text may not.
    code = str(error)
    if not re.fullmatch(r'(?:unreviewed_|catalog_|ai_catalog_|source_import_)[a-z0-9_]{1,100}', code):
        primary = getattr(getattr(error, 'diag', None), 'message_primary', '')
        code = primary if re.fullmatch(r'(?:unreviewed_|catalog_|ai_catalog_)[a-z0-9_]{1,100}',
                                      primary or '') else fallback
    result = {'code': code, 'exception_type': type(error).__name__}
    state = getattr(error, 'sqlstate', None)
    if isinstance(state, str) and re.fullmatch(r'[A-Z0-9]{5}', state):
        result['sqlstate'] = state
    return result


def _aliases(value: PolicyAnalysisInput) -> tuple[str, ...]:
    names = {value.policy_id}
    for binding in value.collection_sources:
        if binding.source == 'youthcenter':
            names.update(('youth:' + binding.external_id, 'youth:youthcenter:' + binding.external_id))
    return tuple(sorted(names))


def _existing_status(item: _Prepared, identities: Sequence[dict]) -> str | None:
    aliases = _aliases(item.value)
    external_ids = {b.external_id for b in item.value.collection_sources if b.source == 'youthcenter'}
    matches = [row for row in identities if row['policy_id'] in aliases or
               (row.get('source') == 'youthcenter' and row.get('external_id') in external_ids)]
    if not matches:
        return None
    # Another alias is never treated as this record, even if the content is equal.
    if all(row['policy_id'] == item.value.policy_id for row in matches) and any(
        row['publication_status'] == 'published'
        and row['document_version'] == item.entry['document_version']
        and row['record_sha256'] == item.entry['record_sha256']
        and row['review_type'] == 'unreviewed' and row['review_status'] == 'unreviewed'
        for row in matches
    ):
        return 'replayed'
    return 'protected_existing'


def run_source_catalog_import(
    values: Sequence[PolicyAnalysisInput], *, root: Path, inventory: dict, selection: dict,
    compile_source: Callable[[PolicyAnalysisInput], Compilation],
    validate: Callable[[dict, str], tuple[object, str]],
    load_existing: Callable[[tuple[str, ...], tuple[str, ...]], Sequence[dict]],
    save: Callable[[Path, object], None], load: Callable[[Path], dict | None],
    publish: Callable[[list[tuple[object, str]]], None] | None = None,
) -> dict:
    """Prepare unique records and optionally insert only new unreviewed releases.

    Publication uses at most ten records per transaction. A failed transaction is
    retried as individual records, with fresh identity checks before each retry.
    ``save`` must atomically replace private JSON files; all I/O is injected.
    """
    unique = {}
    duplicates = 0
    for value in values:
        if value.policy_id in unique:
            if unique[value.policy_id].fingerprint() != value.fingerprint():
                raise ValueError('source_import_conflicting_policy_inputs')
            duplicates += 1
        else:
            unique[value.policy_id] = value
    entries = []
    prepared = []

    def snapshot(complete: bool) -> dict:
        statuses = Counter(entry['status'] for entry in entries)
        source_counts = inventory.get('source_counts', {}).get('youthcenter', {})
        counts = {
            'source_total': source_counts.get('total', 0),
            'source_active': source_counts.get('active', 0),
            'scope_targets': selection.get('counts', {}).get('targets', len(unique)),
            'selected': len(unique), 'duplicate_inputs': duplicates,
            **{key: statuses[key] for key in (
                'prepared', 'loaded_unreviewed', 'replayed', 'protected_existing', 'held', 'failed',
            )},
            'selection_held': selection.get('counts', {}).get('held', 0),
            'out_of_scope': selection.get('counts', {}).get('out_of_scope', 0),
            'calculation_published': 0,
        }
        counts['held'] += counts['selection_held']
        reasons = Counter(selection.get('reason_counts', {}))
        reasons.update(reason for entry in entries for reason in entry['reasons'])
        result = {
            'format_version': 1, 'mode': 'source_only', 'model_analysis': 'not_run',
            'dry_run': publish is None, 'complete': complete, 'inventory': inventory,
            'selection': selection, 'counts': counts, 'reason_counts': dict(reasons),
            'entries': entries,
        }
        save(root / 'report.json', result)
        return result

    def checkpoint(item: _Prepared) -> None:
        save(item.checkpoint, {
            'format_version': 1, **item.entry, 'payload': item.payload,
            'normalized': item.normalized, 'model_analysis': 'not_run',
        })

    for value in unique.values():
        entry = {'policy_id': value.policy_id, 'input_sha256': value.fingerprint(),
                 'record_sha256': None, 'status': 'held', 'reasons': [], 'attempts': 0}
        payload = None
        normalized = {}
        record = None
        try:
            compiled = compile_source(value)
            normalized = compiled.normalized
            if compiled.payload is None or compiled.reasons:
                entry['reasons'] = list(compiled.reasons) or ['source_import_preparation_held']
            else:
                payload = compiled.payload
                record, digest = validate(payload, value.documents[0].text)
                entry.update(record_sha256=digest, status='prepared',
                             document_version=payload['benefit']['source']['document_version'],
                             source_sha256=payload['source_sha256'])
        except Exception as error:
            diagnostic = _error(error, 'source_import_preparation_failed')
            entry.update(status='failed', reasons=[diagnostic['code']], error=diagnostic)
        key = _fingerprint({'record': entry['record_sha256'], 'input': entry['input_sha256']})
        path = root / 'records' / key / 'checkpoint.json'
        prior = load(path)
        if prior and prior.get('input_sha256') == entry['input_sha256'] and prior.get('record_sha256') == entry['record_sha256']:
            attempts = prior.get('attempts', 0)
            entry['attempts'] = attempts if type(attempts) is int and attempts >= 0 else 0
            entry['previous_status'] = prior.get('status')
        entries.append(entry)
        item = _Prepared(value, record, value.documents[0].text, entry, path, payload, normalized)
        checkpoint(item)
        if entry['status'] == 'prepared':
            prepared.append(item)
    snapshot(False)

    def mark_existing(item: _Prepared, identities: Sequence[dict]) -> bool:
        status = _existing_status(item, identities)
        if status is None:
            return False
        item.entry.update(status=status, reasons=(
            ['source_import_protected_existing'] if status == 'protected_existing' else []
        ))
        checkpoint(item)
        return True

    def send(group: list[_Prepared]) -> None:
        policy_ids = tuple(sorted({alias for item in group for alias in _aliases(item.value)}))
        external_ids = tuple(sorted({binding.external_id for item in group
                                     for binding in item.value.collection_sources
                                     if binding.source == 'youthcenter'}))
        try:
            identities = load_existing(policy_ids, external_ids)
        except Exception as error:
            diagnostic = _error(error, 'source_import_existing_lookup_failed')
            for item in group:
                item.entry.update(status='failed', reasons=[diagnostic['code']], error=diagnostic)
                checkpoint(item)
            return
        pending = [item for item in group if not mark_existing(item, identities)]
        if not pending or publish is None:
            return
        for item in pending:
            item.entry['attempts'] += 1
            checkpoint(item)
        try:
            publish([(item.record, item.source) for item in pending])
        except Exception as error:
            diagnostic = _error(error, 'source_import_publication_failed')
            if len(pending) > 1:
                for item in pending:
                    item.entry['batch_error'] = diagnostic
                    send([item])
            else:
                item = pending[0]
                # A commit may have succeeded despite a lost response. Recheck
                # the DB before recording a failure or authorizing any retry.
                try:
                    if mark_existing(item, load_existing(policy_ids, external_ids)):
                        return
                except Exception as lookup_error:
                    item.entry['lookup_error'] = _error(lookup_error, 'source_import_existing_lookup_failed')
                item.entry.update(status='failed', reasons=[diagnostic['code']], error=diagnostic)
                checkpoint(item)
        else:
            for item in pending:
                item.entry.update(status='loaded_unreviewed', reasons=[])
                checkpoint(item)

    for offset in range(0, len(prepared), 10):
        send(prepared[offset:offset + 10])
        snapshot(False)
    return snapshot(True)
