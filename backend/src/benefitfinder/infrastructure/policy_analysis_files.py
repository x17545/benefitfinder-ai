"""Private, immutable per-attempt F12 artifacts. DB status controls review readiness."""

from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re

from benefitfinder.application.policy_analysis import (
    AnalysisClaim, AnalysisStep, PolicyAnalysisResult,
)

_TOOLS = {'fetch_document', 'find_clauses', 'lookup_policy', 'extract_rule',
          'check_missing', 'save_candidate'}


def _encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       indent=2) + '\n').encode()


def _create(path: Path, value: object) -> None:
    with path.open('xb') as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(_encode(value))
        handle.flush()
        os.fsync(handle.fileno())


class PolicyAnalysisFiles:
    def __init__(self, data_dir: Path,
                 write_reviews: Callable[[Path, AnalysisClaim, PolicyAnalysisResult], None],
                 *, model: dict[str, str] | None = None) -> None:
        self._root = data_dir.resolve()
        self._write_reviews = write_reviews
        self._model = dict(model or {})

    def _directory(self, claim: AnalysisClaim) -> Path:
        if (type(claim.task_id) is not int or claim.task_id < 1
            or re.fullmatch(r'[0-9a-f]{32}', claim.token) is None):
            raise ValueError('f12_artifact_failed')
        path = self._root / 'f12-analysis' / str(claim.task_id) / claim.token
        if not path.resolve().is_relative_to(self._root):
            raise ValueError('f12_artifact_failed')
        return path

    def start(self, claim: AnalysisClaim) -> None:
        try:
            directory = self._directory(claim)
            directory.parent.mkdir(parents=True, exist_ok=True)
            directory.mkdir(mode=0o700)
            _create(directory / 'input.json', {'format_version': 1, **claim.input.payload()})
            # Create the trace before any call; even zero-tool failures have a record.
            with (directory / 'trace.jsonl').open('xb') as handle:
                os.fchmod(handle.fileno(), 0o600)
        except (OSError, ValueError):
            raise ValueError('f12_artifact_failed') from None

    def step(self, claim: AnalysisClaim, step: AnalysisStep) -> None:
        try:
            if (step.tool not in _TOOLS or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', step.outcome) is None
                or type(step.elapsed_ms) is not int or not 0 <= step.elapsed_ms <= 3_600_000):
                raise ValueError
            encoded = json.dumps(asdict(step), separators=(',', ':')).encode() + b'\n'
            descriptor = os.open(self._directory(claim) / 'trace.jsonl',
                                 os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'ab') as handle:
                if os.fstat(handle.fileno()).st_size > 65_536:
                    raise ValueError
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, ValueError, AttributeError):
            raise ValueError('f12_artifact_failed') from None

    def finish(self, claim: AnalysisClaim, result: PolicyAnalysisResult) -> tuple[str, str, dict]:
        try:
            directory = self._directory(claim)
            if (len(result.facts) != len(claim.input.documents)
                or {fact.document for fact in result.facts} != set(claim.input.documents)
                or any(not rule.citations or len({cite.document_id for cite in rule.citations}) != 1
                       for rule in result.rules)):
                raise ValueError
            if tuple(json.loads(line) for line in (directory / 'trace.jsonl').read_text().splitlines()) != tuple(asdict(step) for step in result.trace):
                raise ValueError
            self._write_reviews(directory, claim, result)
            _create(directory / 'additional-evidence.json', {
                'format_version': 1, 'status': 'needs_review',
                'additional': [asdict(item) for item in result.additional],
                'missing': [asdict(item) for item in result.missing],
            })
            files = {}
            for path in sorted(directory.rglob('*')):
                if path.is_symlink():
                    raise ValueError
                if path.is_file():
                    files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            summary = {'documents': len(result.facts), 'rules': len(result.rules),
                       'additional': len(result.additional), 'missing': len(result.missing),
                       'tool_calls': len(result.trace)}
            _create(directory / 'manifest.json', {
                'format_version': 1, 'task_id': claim.task_id, 'attempt': claim.attempt,
                'input_sha256': claim.input.fingerprint(), 'origin_recheck_id': claim.origin_recheck_id,
                'agent_version': 'f12-tools-v1', 'model': self._model,
                'artifact_status': 'needs_review', 'summary': summary, 'files': files,
            })
            digest = hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest()
            return f'f12-analysis/{claim.task_id}/{claim.token}', digest, summary
        except Exception:
            raise ValueError('f12_artifact_failed') from None

    def failure(self, claim: AnalysisClaim, code: str) -> None:
        if code not in {'f12_analysis_failed', 'f12_analysis_timeout', 'f12_artifact_failed'}:
            code = 'f12_analysis_failed'
        directory = self._directory(claim)
        if directory.is_dir() and not (directory / 'failure.json').exists():
            _create(directory / 'failure.json', {'code': code, 'attempt': claim.attempt})
