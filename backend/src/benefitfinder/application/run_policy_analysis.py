"""Bounded offline queue processing with injected stores, analysis and artifacts."""

from collections.abc import Callable
from typing import Protocol

from benefitfinder.application.policy_analysis import (
    AnalysisClaim, AnalysisStep, PolicyAnalysisResult, PolicyAnalysisStoreError,
)


class AnalysisTasks(Protocol):
    def claim(self) -> AnalysisClaim | None: ...
    def is_current(self, claim: AnalysisClaim) -> bool: ...
    def complete(self, claim: AnalysisClaim, *, output_ref: str, output_sha256: str,
                 summary: dict[str, object]) -> str: ...
    def fail(self, claim: AnalysisClaim, code: str) -> str: ...


class AnalysisArtifacts(Protocol):
    def start(self, claim: AnalysisClaim) -> None: ...
    def step(self, claim: AnalysisClaim, step: AnalysisStep) -> None: ...
    def finish(self, claim: AnalysisClaim, result: PolicyAnalysisResult) -> tuple[str, str, dict]: ...
    def failure(self, claim: AnalysisClaim, code: str) -> None: ...


def run_pending_analysis(
    tasks: AnalysisTasks,
    artifacts: AnalysisArtifacts,
    analyze: Callable[[AnalysisClaim, Callable[[AnalysisStep], None]], PolicyAnalysisResult],
    *, limit: int = 5,
) -> dict[str, int]:
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('invalid_f12_analysis_limit')
    counts = {'attempted': 0, 'needs_review': 0, 'stale': 0, 'failed': 0}
    for _ in range(limit):
        claim = tasks.claim()
        if claim is None:
            break
        counts['attempted'] += 1
        try:
            artifacts.start(claim)
            if not tasks.is_current(claim):
                raise PolicyAnalysisStoreError('f12_source_changed')
            result = analyze(claim, lambda step: artifacts.step(claim, step))
            if not tasks.is_current(claim):
                raise PolicyAnalysisStoreError('f12_source_changed')
            reference, digest, summary = artifacts.finish(claim, result)
            status = tasks.complete(claim, output_ref=reference, output_sha256=digest,
                                    summary=summary)
            counts['needs_review' if status == 'needs_review' else 'stale'] += 1
        except Exception as error:
            code = str(error)
            if code not in {'f12_analysis_timeout', 'f12_artifact_failed'}:
                code = 'f12_analysis_failed'
            # Partial tool traces remain local even when output or DB completion fails.
            try:
                artifacts.failure(claim, code)
            except Exception:
                pass
            status = tasks.fail(claim, code)
            counts['stale' if status == 'stale' else 'failed'] += 1
    return counts
