"""Offline native Decider adapters. These are never assembled in HTTP runtime."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from benefitfinder.application.decider import (
    DeciderError, Decisions, MANIFEST_SHA256, METADATA_FILES, MODEL_ID, MODEL_REVISION,
    QUESTION_VERSION, canonical_hash,
)


def private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, allow_nan=False)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def validate_responses(requests: list[dict], responses: object) -> list[dict]:
    if not isinstance(responses, list) or len(responses) != len(requests):
        raise DeciderError("decider_response_count_mismatch")
    for request, response in zip(requests, responses, strict=True):
        if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
            raise DeciderError("decider_invalid_response")
        if set(response["answers"]) != set(request["questions"]):
            raise DeciderError("decider_answer_missing")
        for key, question in request["questions"].items():
            answer = response["answers"][key]
            if not isinstance(answer, dict):
                raise DeciderError("decider_invalid_answer")
            kind = question["type"]
            if kind == "locate":
                spans = answer.get("spans")
                if not isinstance(spans, list) or len(spans) > 1024:
                    raise DeciderError("decider_invalid_spans")
                for span in spans:
                    if (not isinstance(span, dict)
                        or type(span.get("start")) is not int or type(span.get("end")) is not int
                        or not 0 <= span["start"] < span["end"] <= len(request["state"])
                        or request["state"][span["start"]:span["end"]] != span.get("text")):
                        raise DeciderError("decider_evidence_mismatch")
                    _number(span.get("score"), 0, 1)
            elif kind == "choice":
                if answer.get("choice") not in question["criteria"]:
                    raise DeciderError("decider_invalid_choice")
            elif kind == "noul":
                _number(answer.get("noul"), 0, 1)
            elif kind == "score":
                _number(answer.get("score"), 0, len(question["criteria"]) - 1)
            else:
                raise DeciderError("decider_unsupported_question")
        usage = response.get("usage", {})
        if any(type(usage.get(key)) is not int or usage[key] < 0
               for key in ("input_tokens", "output_tokens", "sequences")):
            raise DeciderError("decider_invalid_usage")
    return responses


def _number(value: object, minimum: float, maximum: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise DeciderError("decider_invalid_probability")


def verified_checkpoint(path: Path) -> None:
    """Recheck every pinned file before importing native downloaded source."""
    if (path / "revision.txt").read_text().strip() != MODEL_REVISION:
        raise DeciderError("decider_revision_mismatch")
    manifest_bytes = (path / "release-manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise DeciderError("decider_manifest_hash_mismatch")
    manifest = json.loads(manifest_bytes)
    if manifest.get("repo_id") != MODEL_ID:
        raise DeciderError("decider_manifest_mismatch")
    for name, expected in manifest["files"].items():
        expected = METADATA_FILES.get(name, expected)
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise DeciderError("decider_manifest_path_invalid")
        file = path / name
        if file.stat().st_size != expected["bytes"]:
            raise DeciderError("decider_checkpoint_hash_mismatch")
        with file.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != expected["sha256"]:
                raise DeciderError("decider_checkpoint_hash_mismatch")


class LocalDecider:
    def __init__(self, checkpoint: Path, *, max_length: int = 3072,
                 batch_tokens: int = 2048, mode: str = "separate",
                 locate_threshold: float = 0.5) -> None:
        if (not 256 <= max_length <= 32768 or not 256 <= batch_tokens <= 65536
            or mode not in {"separate", "isolated"}
            or type(locate_threshold) not in (float, int)
            or not 0 < locate_threshold <= 1):
            raise ValueError("decider_invalid_memory_limits")
        verified_checkpoint(checkpoint)
        import torch

        if not torch.cuda.is_available():
            raise DeciderError("decider_cuda_required")
        sys.path.insert(0, str(checkpoint.resolve() / "source" / "src"))
        from pplx_decider import Decider

        self.torch = torch
        self.model = Decider.from_pretrained(checkpoint, device="cuda", fast=False, locate=True,
                                             mode=mode, max_length=max_length,
                                             batch_tokens=batch_tokens)
        from pplx_decider.locate import LocateAdapter

        self.model.locate = LocateAdapter(self.model, checkpoint / "locate-span")
        self.model.locate.cutoff = locate_threshold
        self.locate_threshold = locate_threshold
        self.initial_max_length = max_length
        self.initial_batch_tokens = batch_tokens
        self.metrics: list[dict] = []

    @property
    def identity(self) -> dict[str, str]:
        return {"id": MODEL_ID, "revision": MODEL_REVISION, "questions": QUESTION_VERSION,
                "provider": "local", "mode": self.model.mode, "fast": "false",
                "locate_threshold": str(self.locate_threshold),
                "max_length": str(self.initial_max_length),
                "batch_tokens": str(self.initial_batch_tokens)}

    def decide(self, requests: list[dict]) -> list[dict]:
        if not requests:
            return []
        started = monotonic()
        self.torch.cuda.reset_peak_memory_stats()
        oom = False
        try:
            result = self.model.decide(requests)
            return validate_responses(requests, result)
        except self.torch.cuda.OutOfMemoryError:
            oom = True
        except Exception as error:
            if isinstance(error, DeciderError):
                raise
            if "LengthError" in type(error).__name__:
                raise DeciderError("decider_input_length_exceeded") from None
            raise DeciderError("decider_inference_failed") from None
        finally:
            self.torch.cuda.synchronize()
            free, total = self.torch.cuda.mem_get_info()
            self.metrics.append({"requests": len(requests), "batch_tokens": self.model.batch_tokens,
                                 "max_length": self.model.max_length,
                                 "peak_allocated_bytes": self.torch.cuda.max_memory_allocated(),
                                 "peak_reserved_bytes": self.torch.cuda.max_memory_reserved(),
                                 "device_free_bytes": free, "device_total_bytes": total,
                                 "elapsed_seconds": round(monotonic() - started, 3), "oom": oom})
        # Release the failed graph before retrying. No request text is shortened.
        self.torch.cuda.empty_cache()
        if self.model.batch_tokens > 256:
            self.model.batch_tokens = max(256, self.model.batch_tokens // 2)
            return self.decide(requests)
        if len(requests) > 1:
            middle = len(requests) // 2
            return self.decide(requests[:middle]) + self.decide(requests[middle:])
        if self.model.max_length > 512:
            # Native Locate splits question groups at this limit and keeps the
            # complete state. If even one full-state question cannot fit, fail.
            self.model.max_length = max(512, self.model.max_length // 2)
            return self.decide(requests)
        raise DeciderError("decider_gpu_memory_exceeded")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HostedDecider:
    """A verified native handler endpoint, never a chat/completions substitute."""

    def __init__(self, url: str, key: str, *, budget_path: Path, daily_calls: int = 100,
                 timeout: int = 120) -> None:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in url) or not key.strip()
            or "\n" in key or "\r" in key or not 1 <= daily_calls <= 10000
            or not 1 <= timeout <= 300):
            raise ValueError("decider_invalid_endpoint_settings")
        self.url, self.key = url, key
        self.budget_path, self.daily_calls, self.timeout = budget_path, daily_calls, timeout
        self.capabilities = self._post({"operation": "capabilities"})
        if (not isinstance(self.capabilities, dict)
            or self.capabilities.get("model") != MODEL_ID
            or self.capabilities.get("revision") != MODEL_REVISION
            or self.capabilities.get("offsets") != "unicode_codepoints"
            or self.capabilities.get("fast") is not False
            or set(self.capabilities.get("tasks", [])) != {"locate", "choice", "noul", "score"}):
            raise DeciderError("decider_endpoint_incompatible")
        limits = self.capabilities.get("limits", {})
        if (not isinstance(limits, dict) or type(limits.get("max_requests")) is not int
            or not 1 <= limits["max_requests"] <= 32):
            raise DeciderError("decider_endpoint_limits_required")
        self.max_requests = limits["max_requests"]

    @property
    def identity(self) -> dict[str, str]:
        return {"id": MODEL_ID, "revision": MODEL_REVISION, "questions": QUESTION_VERSION,
                "provider": "external_api", "mode": "separate", "fast": "false"}

    def _reserve(self) -> None:
        # Caller holds the batch's OS lock, including the budget read/replace.
        day = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        value = json.loads(self.budget_path.read_text()) if self.budget_path.exists() else {}
        calls = value.get("calls", 0) if value.get("date") == day else 0
        if type(calls) is not int or calls < 0:
            raise DeciderError("decider_budget_invalid")
        if calls >= self.daily_calls:
            raise DeciderError("decider_daily_budget_exceeded")
        private_json(self.budget_path, {"date": day, "calls": calls + 1})

    def _post(self, value: dict) -> dict:
        body = json.dumps({"inputs": value}, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > 4 * 1024 * 1024:
            raise DeciderError("decider_request_size_exceeded")
        for attempt in range(3):
            self._reserve()  # Includes probes, HTTP retries and ambiguous timeouts.
            request = Request(self.url, data=body, headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + self.key}, method="POST")
            try:
                with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise DeciderError("decider_response_size_exceeded")
                return json.loads(raw)
            except HTTPError as error:
                transient = error.code in (429, 502, 503, 504)
            except (URLError, TimeoutError, OSError):
                transient = True
            except (ValueError, RecursionError):
                raise DeciderError("decider_invalid_response") from None
            if not transient or attempt == 2:
                raise DeciderError("decider_endpoint_failed") from None
            sleep(2 ** attempt)
        raise DeciderError("decider_endpoint_failed")

    def decide(self, requests: list[dict]) -> list[dict]:
        responses = []
        for index in range(0, len(requests), self.max_requests):
            group = requests[index:index + self.max_requests]
            result = self._post({"operation": "decide", "requests": group})
            if not isinstance(result, dict):
                raise DeciderError("decider_invalid_response")
            responses.extend(validate_responses(group, result.get("responses")))
        return responses


class CheckpointDecider:
    """Save validated responses separately, keyed by model, questions and full state."""

    def __init__(self, client: Decisions, root: Path) -> None:
        self.client, self.root = client, root

    @property
    def identity(self) -> dict[str, str]:
        return self.client.identity

    def decide(self, requests: list[dict]) -> list[dict]:
        paths = [self.root / (canonical_hash({"model": self.identity, "request": r}) + ".json")
                 for r in requests]
        results: list[dict | None] = [None] * len(requests)
        missing = []
        for i, path in enumerate(paths):
            if path.exists():
                saved = json.loads(path.read_text())
                if saved.get("request_sha256") != canonical_hash(requests[i]):
                    raise DeciderError("decider_checkpoint_mismatch")
                results[i] = validate_responses([requests[i]], [saved["response"]])[0]
            else:
                missing.append(i)
        if missing:
            responses = self.client.decide([requests[i] for i in missing])
            for i, response in zip(missing, responses, strict=True):
                private_json(paths[i], {"request_sha256": canonical_hash(requests[i]),
                                        "response": response})
                results[i] = response
        return validate_responses(requests, results)
