"""Bootstrap API tests: real HTTP status, contracts, and error shapes.

No DB, LLM, network, or real collection data is used. Routes that validate
input or raise are registered inside tests on the app instance returned by
``create_app`` and never ship in production.
"""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jsonschema import validate
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

import benefitfinder.main as main_module
from benefitfinder.bootstrap import create_app

CLONE_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = CLONE_ROOT / "contracts" / "v1"


def load_contract(name: str) -> dict[str, Any]:
    """Load a shared JSON Schema contract from the local contracts directory."""
    return json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))


class EchoInput(BaseModel):
    """Test-only input model used to trigger request validation."""

    age: int = Field(ge=0, le=150)


def add_validation_route(app: FastAPI) -> None:
    """Register a test-only echo route on the given app instance."""

    @app.post("/_test/echo")
    def echo(body: EchoInput) -> dict[str, int]:
        return {"age": body.age}


def add_boom_route(app: FastAPI) -> None:
    """Register a test-only crashing route on the given app instance."""

    @app.get("/_test/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("boom-secret-should-never-leak")


class BootstrapApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def assertErrorShape(self, response: Any, status: int, code: str) -> dict[str, Any]:
        """Check status, common error shape, contract, and request id format."""
        self.assertEqual(response.status_code, status)
        body = response.json()
        self.assertEqual(set(body.keys()), {"request_id", "code", "message"})
        self.assertEqual(body["code"], code)
        self.assertTrue(body["message"])
        uuid.UUID(body["request_id"], version=4)
        validate(body, load_contract("error.schema.json"))
        return body

    def test_health_matches_contract(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        validate(response.json(), load_contract("health.schema.json"))

    def test_docs_and_openapi_available(self) -> None:
        self.assertEqual(self.client.get("/docs").status_code, 200)
        spec_response = self.client.get("/openapi.json")
        self.assertEqual(spec_response.status_code, 200)
        self.assertIn("/health", spec_response.json()["paths"])

    def test_openapi_health_schema_matches_contract(self) -> None:
        spec = self.client.get("/openapi.json").json()
        operation = spec["paths"]["/health"]["get"]
        ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        contract = load_contract("health.schema.json")
        self.assertEqual(schema.get("required"), contract["required"])
        self.assertIs(schema.get("additionalProperties"), False)
        status = schema["properties"]["status"]
        allowed = set(status.get("enum", [status.get("const")]))
        self.assertEqual(allowed, {"ok"})

    def test_openapi_error_schema_matches_contract(self) -> None:
        spec = self.client.get("/openapi.json").json()
        contract = load_contract("error.schema.json")
        candidates = [
            schema
            for schema in spec["components"]["schemas"].values()
            if set(schema.get("required", [])) == {"request_id", "code", "message"}
        ]
        self.assertTrue(candidates)
        schema = candidates[0]
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertEqual(
            set(schema["properties"]["code"]["enum"]),
            set(contract["properties"]["code"]["enum"]),
        )

    def test_not_found_uses_common_error(self) -> None:
        response = self.client.get("/api/v1/no-such-route")
        self.assertErrorShape(response, 404, "not_found")
        self.assertNotIn("no-such-route", response.text)

    def test_wrong_method_preserves_allow_header(self) -> None:
        response = self.client.post("/health")
        self.assertErrorShape(response, 405, "method_not_allowed")
        self.assertIn("GET", response.headers["allow"])

    def test_http_errors_preserve_status_and_headers_without_exposing_detail(self) -> None:
        cases = (
            (400, "http_error"), (401, "http_error"), (403, "http_error"),
            (404, "not_found"), (405, "method_not_allowed"), (409, "http_error"),
            (418, "http_error"), (422, "invalid_request"), (429, "http_error"),
            (500, "internal_error"), (502, "http_error"),
            (503, "dependency_unavailable"), (504, "http_error"),
        )
        secret = "private-detail-must-not-leak"
        headers = {
            "Retry-After": "30",
            "WWW-Authenticate": 'Bearer realm="test"',
            "Allow": "GET",
            "X-Test-Context": "public-context",
        }
        for exception_type in (HTTPException, StarletteHTTPException):
            app = create_app()

            @app.get("/_test/http-error/{status}")
            def raise_http_error(status: int) -> None:
                # FastAPI permits structured detail; Starlette usually uses text.
                detail = {"private": secret} if exception_type is HTTPException else secret
                raise exception_type(status_code=status, detail=detail, headers=headers)

            with TestClient(app, raise_server_exceptions=False) as client:
                for status, code in cases:
                    with self.subTest(exception=exception_type.__module__, status=status):
                        response = client.get(f"/_test/http-error/{status}")
                        self.assertErrorShape(response, status, code)
                        for name, value in headers.items():
                            self.assertEqual(response.headers[name], value)
                        self.assertNotIn(secret, response.text)
                        self.assertNotIn("detail", response.json())

    def test_http_error_rebuilds_headers_for_the_new_json_body(self) -> None:
        @self.app.get("/_test/encoded-error")
        def encoded_error() -> None:
            raise HTTPException(
                status_code=503,
                detail="private-old-body",
                headers={
                    "CoNtEnT-TyPe": "text/plain",
                    "CONTENT-LENGTH": "9999",
                    "Content-Encoding": "gzip",
                    "Transfer-Encoding": "chunked",
                    "Retry-After": "60",
                },
            )

        response = self.client.get("/_test/encoded-error")
        self.assertErrorShape(response, 503, "dependency_unavailable")
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(int(response.headers["content-length"]), len(response.content))
        self.assertNotIn("content-encoding", response.headers)
        self.assertNotIn("transfer-encoding", response.headers)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertNotIn("private-old-body", response.text)

    def test_request_ids_are_unique(self) -> None:
        first = self.client.get("/api/v1/no-such-route").json()["request_id"]
        second = self.client.get("/api/v1/no-such-route").json()["request_id"]
        self.assertNotEqual(first, second)

    def test_validation_error_hides_input(self) -> None:
        add_validation_route(self.app)
        valid = self.client.post("/_test/echo", json={"age": 20})
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json(), {"age": 20})
        secret = "rrn-secret-990101-1234567"
        response = self.client.post("/_test/echo", json={"age": secret})
        self.assertErrorShape(response, 422, "invalid_request")
        self.assertNotIn(secret, response.text)

    def test_unexpected_error_is_generic(self) -> None:
        add_boom_route(self.app)
        response = self.client.get("/_test/boom")
        self.assertErrorShape(response, 500, "internal_error")
        self.assertNotIn("boom-secret", response.text)
        self.assertNotIn("RuntimeError", response.text)

    def test_virtual_routes_do_not_leak(self) -> None:
        add_validation_route(self.app)
        add_boom_route(self.app)
        fresh_client = TestClient(create_app(), raise_server_exceptions=False)
        self.assertEqual(
            fresh_client.post("/_test/echo", json={"age": 20}).status_code, 404
        )
        self.assertEqual(fresh_client.get("/_test/boom").status_code, 404)

    def test_main_app_exposes_only_health(self) -> None:
        client = TestClient(main_module.app, raise_server_exceptions=False)
        self.assertEqual(client.get("/health").json(), {"status": "ok"})
        paths = client.get("/openapi.json").json()["paths"]
        self.assertIn("/health", paths)
        self.assertFalse([path for path in paths if path.startswith("/_test")])


if __name__ == "__main__":
    unittest.main()
