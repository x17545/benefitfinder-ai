"""Validate a create-only F12 approved export without inventing review decisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ApprovedItem:
    item_id: str
    kind: str
    reviewer: str
    reviewed_at: datetime
    reason: str
    quote: str
    location: str
    candidate: dict[str, Any]


@dataclass(frozen=True)
class ApprovedDocument:
    raw_policy_id: str
    policy_id: str
    name: str
    category: str
    version: str
    url: str
    checked_at: datetime
    pair_sha256: str
    content_sha256: str
    export_sha256: str
    items: tuple[ApprovedItem, ...]


def _text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= maximum:
        raise ValueError("invalid_f12_approved_export")
    return value


def _time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, 64))
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise ValueError("invalid_f12_review_time") from None


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("invalid_f12_export_hash")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_f12_export_key")
        result[key] = value
    return result


def load_approved_export(path: Path, *, name: str, category: str) -> ApprovedDocument:
    raw = path.read_bytes()
    try:
        data = json.loads(raw, object_pairs_hook=_unique)
        if set(data) != {
            "format_version", "kind", "pair_sha256", "policy_id", "source", "items"
        } or type(data["format_version"]) is not int or data["format_version"] != 1:
            raise ValueError
        if data["kind"] != "f12_approved_review_export" or category not in {
            "employment", "housing"
        }:
            raise ValueError
        raw_id = _text(data["policy_id"], 90)
        if not re.fullmatch("[A-Za-z0-9:_-]+", raw_id) or raw_id.startswith("youth:example:"):
            raise ValueError
        policy_id = raw_id if raw_id.startswith("youth:") else "youth:" + raw_id
        source = data["source"]
        if set(source) != {"id", "url", "document_version", "content_sha256", "collected_at"}:
            raise ValueError
        url = _text(source["url"], 2048)
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError
        version = _text(source["document_version"], 120)
        content_hash = _hash(source["content_sha256"])
        if version.startswith("sha256:") and version != "sha256:" + content_hash:
            raise ValueError
        checked_at = _time(source["collected_at"])
        if not isinstance(data["items"], list) or not 1 <= len(data["items"]) <= 100:
            raise ValueError
        items = []
        for entry in data["items"]:
            if set(entry) != {"item_id", "kind", "approval", "candidate"}:
                raise ValueError
            kind = entry["kind"]
            item_id = _text(entry["item_id"], 120)
            if kind not in {"rule", "eligibility", "amount", "timing"} or not re.fullmatch(
                re.escape(kind) + r":[1-9][0-9]*", item_id
            ):
                raise ValueError
            approval = entry["approval"]
            if set(approval) != {"reviewer", "reason", "reviewed_at"}:
                raise ValueError
            reviewed_at = _time(approval["reviewed_at"])
            if not checked_at <= reviewed_at <= datetime.now().astimezone():
                raise ValueError
            candidate = entry["candidate"]
            if not isinstance(candidate, dict):
                raise ValueError
            citations = candidate.get("citations") if kind == "rule" else [candidate.get("evidence")]
            if not isinstance(citations, list) or not citations:
                raise ValueError
            if kind == "rule" and candidate.get("source_policy_id") != raw_id:
                raise ValueError
            quotes, locations = [], []
            for citation in citations:
                if (
                    not isinstance(citation, dict)
                    or citation.get("document_id") != source["id"]
                    or citation.get("document_version") != version
                    or citation.get("content_sha256") != content_hash
                ):
                    raise ValueError
                quotes.append(_text(citation.get("quote"), 2500))
                locations.append(_text(citation.get("location"), 500))
            items.append(ApprovedItem(
                item_id, kind, _text(approval["reviewer"], 128), reviewed_at,
                _text(approval["reason"], 1000), _text("\n".join(quotes), 2500),
                _text("; ".join(locations), 500), candidate,
            ))
        if len({i.item_id for i in items}) != len(items):
            raise ValueError
        return ApprovedDocument(
            raw_id, policy_id, _text(name, 120), category, version, url, checked_at,
            _hash(data["pair_sha256"]), content_hash, hashlib.sha256(raw).hexdigest(), tuple(items),
        )
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("invalid_f12_approved_export") from None
    except ValueError:
        raise ValueError("invalid_f12_approved_export") from None
