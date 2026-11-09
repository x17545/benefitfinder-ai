"""LocalE4BChat 가상 테스트.

외부 네트워크·실제 모델 호출 없이 mock만 사용한다.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import unittest
from unittest import mock

FIXED_CODE = "chunk_model_failed"
MAX_BYTES = 1024 * 1024
SECRET = "VIRTUAL-SECRET-chunk-e4b-0000"


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, n: int = -1) -> bytes:
        if n is not None and n >= 0:
            return self._body[:n]
        return self._body


class FakeConnection:
    def __init__(self, response=None, *, request_exc=None, response_exc=None, read_exc=None):
        self.response = response
        self.request_exc = request_exc
        self.response_exc = response_exc
        if read_exc is not None and response is not None:
            def failing_read(n=-1):
                raise read_exc
            response.read = failing_read
        self.request_args = None
        self.closed = False

    def request(self, method, url, body=None, headers=None):
        if self.request_exc is not None:
            raise self.request_exc
        self.request_args = {"method": method, "url": url, "body": body, "headers": headers}

    def getresponse(self):
        if self.response_exc is not None:
            raise self.response_exc
        assert self.response is not None
        return self.response

    def close(self):
        self.closed = True


def success_body(content='{"virtual": 1}'):
    return json.dumps({"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": content}}).encode("utf-8")


class LocalE4BChatTest(unittest.TestCase):
    def _call(self, messages, *, status=200, body=None, **fake_kwargs):
        from benefitfinder.infrastructure.chunk_e4b import LocalE4BChat

        raw_body = body if body is not None else success_body()
        fake = FakeConnection(FakeResponse(status, raw_body), **fake_kwargs)
        with mock.patch("benefitfinder.infrastructure.chunk_e4b.http.client.HTTPConnection", return_value=fake) as ctor:
            chat = LocalE4BChat()
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    result = chat(messages, tuple(range(1, 11)))
            except ValueError:
                self.assertEqual(out.getvalue(), "")
                raise
            self.assertEqual(out.getvalue(), "")
            return result, fake, ctor

    def test_success_returns_content_and_fixed_payload(self):
        messages = [{"role": "user", "content": "virtual question"}]
        result, fake, ctor = self._call(messages)
        self.assertEqual(result, '{"virtual": 1}')
        self.assertTrue(fake.closed)
        self.assertIsNotNone(fake.request_args)
        self.assertEqual(fake.request_args["method"], "POST")
        self.assertEqual(fake.request_args["url"], "/api/chat")
        payload = json.loads(bytes(fake.request_args["body"]))
        self.assertEqual(payload["model"], "gemma4:e4b")
        self.assertIs(payload["think"], False)
        self.assertEqual(set(payload["format"]["required"]),
                         {"eligibility", "amount", "timing", "exceptions"})
        self.assertFalse(payload["format"]["additionalProperties"])
        for section in payload["format"]["properties"].values():
            self.assertEqual(section["items"], {"type": "integer", "enum": list(range(1, 11))})
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["options"], {"temperature": 0, "num_ctx": 16384, "num_predict": 4096})
        ctor.assert_called_once_with("127.0.0.1", 11434, timeout=60)

    def test_construct_does_not_connect(self):
        with mock.patch("benefitfinder.infrastructure.chunk_e4b.http.client.HTTPConnection") as ctor:
            from benefitfinder.infrastructure.chunk_e4b import LocalE4BChat

            LocalE4BChat()
            ctor.assert_not_called()

    def test_import_does_not_request(self):
        with mock.patch("benefitfinder.infrastructure.chunk_e4b.http.client.HTTPConnection") as ctor:
            import benefitfinder.infrastructure.chunk_e4b as module

            importlib.reload(module)
            ctor.assert_not_called()

    def test_non_200_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._call([{"role": "user", "content": "virtual"}], status=500, body=b"busy")
        self.assertEqual(str(ctx.exception), FIXED_CODE)

    def test_oversize_rejected(self):
        big = b"a" * (MAX_BYTES + 1)
        with self.assertRaises(ValueError) as ctx:
            self._call([{"role": "user", "content": "virtual"}], body=big)
        self.assertEqual(str(ctx.exception), FIXED_CODE)

    def test_corrupt_json_rejected(self):
        for bad in (b"{not json", b"[1, 2]"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    self._call([{"role": "user", "content": "virtual"}], body=bad)
                self.assertEqual(str(ctx.exception), FIXED_CODE)

    def test_truncated_responses_rejected(self):
        cases = [
            {"done": False, "done_reason": "stop", "message": {"role": "assistant", "content": "x"}},
            {"done": True, "done_reason": "length", "message": {"role": "assistant", "content": "x"}},
            {"done": True, "done_reason": "stop"},
            {"done": True, "done_reason": "stop", "message": {"role": "user", "content": "x"}},
            {"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": "   "}},
            {"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": 123}},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                body = json.dumps(payload).encode("utf-8")
                with self.assertRaises(ValueError) as ctx:
                    self._call([{"role": "user", "content": "virtual"}], body=body)
                self.assertEqual(str(ctx.exception), FIXED_CODE)

    def test_transport_failures_map_to_fixed_code_and_close(self):
        from benefitfinder.infrastructure.chunk_e4b import LocalE4BChat

        failures = [{"request_exc": OSError("down")}, {"response_exc": OSError("down")}, {"read_exc": OSError("down")}]
        for kwargs in failures:
            with self.subTest(kwargs=sorted(kwargs)):
                fake = FakeConnection(FakeResponse(200, success_body()), **kwargs)
                with mock.patch("benefitfinder.infrastructure.chunk_e4b.http.client.HTTPConnection", return_value=fake):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        with self.assertRaises(ValueError) as ctx:
                            LocalE4BChat()([{"role": "user", "content": "virtual"}], tuple(range(1, 11)))
                    self.assertEqual(str(ctx.exception), FIXED_CODE)
                    self.assertEqual(out.getvalue(), "")
                    self.assertTrue(fake.closed)

    def test_constructor_failure_hygiene(self):
        from benefitfinder.infrastructure.chunk_e4b import LocalE4BChat

        with mock.patch("benefitfinder.infrastructure.chunk_e4b.http.client.HTTPConnection", side_effect=OSError("down")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                with self.assertRaises(ValueError) as ctx:
                    LocalE4BChat()([{"role": "user", "content": SECRET}], tuple(range(1, 11)))
            self.assertEqual(str(ctx.exception), FIXED_CODE)
            self.assertNotIn(SECRET, str(ctx.exception))
            self.assertEqual(out.getvalue(), "")

    def test_exception_hides_message_and_body(self):
        secret_body = json.dumps({"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": SECRET}}).encode("utf-8")
        result, _, _ = self._call([{"role": "user", "content": "virtual"}], body=secret_body)
        self.assertEqual(result, SECRET)
        failing = json.dumps({"done": True, "done_reason": "length", "message": {"role": "assistant", "content": SECRET}}).encode()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(ValueError) as ctx:
                self._call([{"role": "user", "content": SECRET}], body=failing)
        self.assertEqual(str(ctx.exception), FIXED_CODE)
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, out.getvalue())


if __name__ == "__main__":
    unittest.main()
