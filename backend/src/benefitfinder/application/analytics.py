"""Privacy-safe analytics delivery interface and bounded best-effort dispatch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

AnalyticsParameter: TypeAlias = str | int | bool


@dataclass(frozen=True, slots=True)
class AnalyticsEventRecord:
    """Validated, scalar-only event data passed across the application boundary."""

    client_id: str
    session_id: int
    name: str
    parameters: Mapping[str, AnalyticsParameter]


class AnalyticsSender(Protocol):
    """Best-effort outbound sender implemented by an infrastructure adapter."""

    async def send(self, event: AnalyticsEventRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class AnalyticsService:
    """Expose consent/config state and isolate analytics failures from user requests."""

    enabled: bool = False
    sender: AnalyticsSender | None = None

    async def deliver(self, event: AnalyticsEventRecord) -> None:
        """Deliver only while enabled; every delivery failure stays outside the API."""
        if not self.enabled or self.sender is None:
            return
        try:
            await self.sender.send(event)
        except Exception:
            # Analytics is best effort. Cancellation and process exit still propagate.
            return
