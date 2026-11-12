"""Read public metadata without reserving an analysis or receiving a profile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from benefitfinder.application.analysis_usage import AnalysisUsage
from benefitfinder.application.published_policies import PublishedPolicyRepository
from benefitfinder.application.published_services import PublishedServiceRepository


class ServiceMetadataUnavailable(RuntimeError):
    """No published snapshot exists yet, or its metadata cannot be read."""


@dataclass(frozen=True)
class ServiceMetadata:
    data_updated_at: datetime
    base_rate: float | None
    base_rate_status: str
    daily_remaining: int
    virtual_data: bool


class ReadServiceMetadata:
    def __init__(
        self,
        usage: AnalysisUsage,
        base_rate: Callable[[], float | None],
        *,
        policy_repository: PublishedPolicyRepository | None,
        example_updated_at: str,
        service_repository: PublishedServiceRepository | None = None,
    ) -> None:
        self.usage = usage
        self.base_rate = base_rate
        self.policy_repository = policy_repository
        self.example_updated_at = example_updated_at
        self.service_repository = service_repository

    def __call__(self) -> ServiceMetadata:
        if self.policy_repository is None and self.service_repository is None:
            timestamps = [self.example_updated_at]
        else:
            # Use the same validated release reader as recommendations, including
            # approval checks. A successful empty read has no honest update time.
            timestamps = ([p.published_at for p in self.policy_repository.list_published()]
                          if self.policy_repository is not None else [])
            if self.service_repository is not None:
                timestamps.extend(p.published_at for p in self.service_repository.list_published())
        try:
            instants = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in timestamps]
            if not instants or any(value.utcoffset() is None for value in instants):
                raise ValueError
            updated_at = max(instants)
        except (TypeError, ValueError, AttributeError):
            raise ServiceMetadataUnavailable("published_metadata_unavailable") from None
        rate = self.base_rate()
        return ServiceMetadata(
            data_updated_at=updated_at,
            base_rate=rate,
            base_rate_status="verified" if rate is not None and self.policy_repository is not None else "unknown",
            daily_remaining=self.usage.remaining(),
            virtual_data=self.policy_repository is None and self.service_repository is None,
        )
