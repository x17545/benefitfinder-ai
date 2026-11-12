"""Published cash-policy catalogue, independent of persistence and HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class PolicyCatalogUnavailable(RuntimeError):
    """Reading the catalogue failed; this is different from an empty catalogue."""


@dataclass(frozen=True)
class PublishedPolicy:
    detail: dict[str, Any]
    conditions: tuple[dict[str, Any], ...]
    payments: tuple[tuple[int, int], ...] | None
    published_at: str

    @property
    def policy_id(self) -> str:
        return self.detail["id"]


class PublishedPolicyRepository(Protocol):
    def list_published(self) -> tuple[PublishedPolicy, ...]: ...

    def get_published(self, policy_id: str) -> PublishedPolicy | None: ...
