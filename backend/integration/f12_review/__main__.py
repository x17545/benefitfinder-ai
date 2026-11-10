"""Explicit local import/serve commands; production main never enables review preview."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.integration.f12_review.artifacts import load_approved_export
from backend.integration.f12_review.database import (
    PreviewPolicy,
    import_approved_documents,
    load_approved_draft_evidence,
    load_preview_catalog,
    migrate_preview_database,
    require_local_test_database,
)
from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer
from benefitfinder.api.schemas.recommendation import RecommendationRequest
from benefitfinder.application.policy_explanations import (
    ExplanationGenerator,
    ExplanationRequest,
    explanation_text,
)
from benefitfinder.bootstrap import HTTP_ERROR_RESPONSES, error_body
from benefitfinder.infrastructure.ollama_explanations import OllamaExplanationCompletion


@dataclass(frozen=True)
class ReviewDescription:
    explanation: dict[str, Any]
    scope: str


async def describe_policy(
    url: str, policy: PreviewPolicy, generator: ExplanationGenerator | None, timeout_seconds: float,
    *, item_id: str | None = None,
) -> ReviewDescription | None:
    """Read fresh draft evidence. Never mark it published or calculate a personal amount."""
    request = ExplanationRequest(policy.policy_id, policy.name, policy.category, policy.version, None)
    row: dict[str, Any] = {
        "policy_id": policy.policy_id,
        "amount_in_period_won": None,
        "original_amount_won": None,
        "amount_status": "unknown",
        "eligibility": [],
        "ai_reason": explanation_text(request),
        "ai_generated": False,
        "ai_sources": [],
        "source": {"url": policy.url, "document_version": policy.version,
                   "checked_at": policy.checked_at.isoformat()},
    }
    scope = policy.scope
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        async with asyncio.timeout(timeout_seconds):
            try:
                evidence = await load_approved_draft_evidence(
                    url, policy, **({"item_ids": (item_id,)} if item_id is not None else {})
                )
            except ValueError:
                if item_id is not None:
                    return None
                raise
            clauses, scope = evidence.clauses, evidence.scope
            if not clauses:
                return None
            if generator is None:
                return ReviewDescription(row, scope)
            # One approved clause per review explanation: avoid blending distinct approval scopes.
            clauses = clauses[:1]
            generated = await generator.generate(
                request, clauses, timeout_seconds=max(0.001, deadline - loop.time())
            )
            if (
                not generated.text.strip() or not generated.source_ids
                or len(set(generated.source_ids)) != len(generated.source_ids)
                or not set(generated.source_ids) <= {c.id for c in clauses}
            ):
                return ReviewDescription(row, scope)
            row["ai_reason"] = explanation_text(request, generated.text)
            row["ai_generated"] = True
            row["ai_sources"] = [
                {"quote": c.text, "location": c.location,
                 "source": {"url": c.source_url, "document_version": c.document_version,
                            "checked_at": c.checked_at.isoformat()}}
                for c in clauses if c.id in generated.source_ids
            ]
    except (TimeoutError, psycopg.Error, OSError, RuntimeError, ValueError):
        # No exception body, original text, DB URL, or profile is logged.
        pass
    return ReviewDescription(row, scope)


def create_preview_app(
    url: str, policies: tuple[PreviewPolicy, ...], generator: ExplanationGenerator | None,
    *, region_catalog: object | None = None, region_router: APIRouter | None = None,
    timeout_seconds: float = 5,
    focus_items: dict[str, str] | None = None,
    focus_documents: dict[str, str] | None = None,
) -> FastAPI:
    require_local_test_database(url)
    if not 0 < timeout_seconds <= 5 or not policies:
        raise ValueError("invalid_review_preview_configuration")
    focus_documents = dict(focus_documents or {})
    versions: dict[str, set[str]] = {}
    for policy in policies:
        versions.setdefault(policy.policy_id, set()).add(policy.version)
    if (
        len({(p.policy_id, p.version) for p in policies}) != len(policies)
        or not set(focus_documents) <= set(versions)
        or any(version not in versions[policy_id]
               for policy_id, version in focus_documents.items())
        or any(len(values) > 1 and policy_id not in focus_documents
               for policy_id, values in versions.items())
    ):
        raise ValueError("invalid_review_preview_document")
    policies = tuple(p for p in policies
                     if p.version == focus_documents.get(p.policy_id, p.version))
    focus_items = focus_items if focus_items is not None else {}
    if not set(focus_items) <= {policy.policy_id for policy in policies} or any(
        not isinstance(item, str) or not re.fullmatch(r"(?:rule|eligibility|amount|timing):[1-9][0-9]*", item)
        for item in focus_items.values()
    ):
        raise ValueError("invalid_review_preview_focus")
    app = FastAPI(title="BenefitFinder local review verification")
    app.state.region_catalog = region_catalog
    if region_router is not None:
        if region_catalog is None:
            raise ValueError("review_preview_region_catalog_required")
        app.include_router(region_router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Any, error: StarletteHTTPException) -> JSONResponse:
        code, message = HTTP_ERROR_RESPONSES.get(error.status_code, ("http_error", "요청 실패"))
        return JSONResponse(error_body(code, message), status_code=error.status_code,
                            headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Any, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(error_body("invalid_request", "요청 값이 올바르지 않습니다."), status_code=422)

    @app.post("/api/v1/recommendations")
    async def recommendations(request: RecommendationRequest) -> dict[str, Any]:
        if request.domain != "youth":
            raise HTTPException(status_code=422, detail="unsupported_domain")
        interests = request.profile.domain_data.interests if request.profile.domain_data else []
        selected = tuple(p for p in policies if p.category in (interests or []))
        explanations = await asyncio.gather(*(
            describe_policy(url, policy, generator, timeout_seconds,
                            item_id=focus_items.get(policy.policy_id)) for policy in selected
        ))
        pending = [
            {"policy_id": policy.policy_id, "eligibility_summary": "needs_review",
             "policy_name": policy.name, "verification_only": True,
             "review_scope": description.scope, "review_explanation": description.explanation}
            for policy, description in zip(selected, explanations, strict=True)
            if description is not None
        ]
        return {
            "request_id": str(uuid4()), "domain": "youth", "view": "empty",
            "results": {period: {
                pv: {"portfolios": [], "calculation_status": "unavailable",
                     "unavailable_reason": "unknown_payment_timing", "not_yet_reviewed": pending}
                for pv in ("pv_off", "pv_on")
            } for period in ("1y", "2y", "all")},
            "ai_generated": any(e.explanation["ai_generated"] for e in explanations if e is not None),
            "data_updated_at": max(p.imported_at for p in policies).isoformat(),
            "discount_rate": None, "discount_rate_status": "unknown",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-only, unpublished F12 review verification")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("migrate")
    importer = actions.add_parser("import-approved")
    importer.add_argument("--document", nargs=3, action="append", required=True,
                          metavar=("CATEGORY", "NAME", "EXPORT"))
    importer.add_argument("--append", action="store_true",
                          help="Reuse an identical draft document; insert only new item IDs")
    server = actions.add_parser("serve")
    server.add_argument("--model", default="gemma4:e4b")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--timeout-seconds", type=float, default=5)
    server.add_argument("--focus-item", nargs=2, action="append", default=[],
                        metavar=("POLICY_ID", "ITEM_ID"))
    server.add_argument("--focus-document", nargs=2, action="append", default=[],
                        metavar=("POLICY_ID", "DOCUMENT_VERSION"))
    args = parser.parse_args()
    url = os.environ.get("F13_REVIEW_TEST_DATABASE_URL", "")
    try:
        require_local_test_database(url)
        if args.action == "migrate":
            migrate_preview_database(url)
            print("전용 로컬 검증 DB의 빈 스키마만 준비했습니다.")
        elif args.action == "import-approved":
            documents = tuple(load_approved_export(Path(path), name=name, category=category)
                              for category, name, path in args.document)
            import_approved_documents(url, documents, append=args.append)
            print(f"승인 근거 {sum(len(d.items) for d in documents)}개를 draft로 적재했습니다.")
        else:
            # Real-region PR #19 is needed only to run the combined browser verification.
            # Keep independent DB/parser tests runnable on the PR #20 branch, without fake regions.
            try:
                from benefitfinder.api.v1.routers.options import router as options_router
                from benefitfinder.infrastructure.region_codes import OfficialRegionCatalog
            except ImportError:
                raise ValueError("review_preview_requires_real_region_api") from None
            catalog = load_preview_catalog(url)
            if len({policy for policy, _ in args.focus_item}) != len(args.focus_item):
                raise ValueError("invalid_review_preview_focus")
            if len({policy for policy, _ in args.focus_document}) != len(args.focus_document):
                raise ValueError("invalid_review_preview_document")
            generator = GroundedPolicyExplainer(OllamaExplanationCompletion(args.model), purpose="review")
            app = create_preview_app(url, catalog, generator, timeout_seconds=args.timeout_seconds,
                                     region_catalog=OfficialRegionCatalog(), region_router=options_router,
                                     focus_items=dict(args.focus_item),
                                     focus_documents=dict(args.focus_document))
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    except (ValueError, OSError, psycopg.Error):
        parser.exit(1, "검증 작업 실패: f12_review_preview_failed\n")


if __name__ == "__main__":
    main()
