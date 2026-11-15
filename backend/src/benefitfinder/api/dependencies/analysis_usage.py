"""Reserve once after HTTP input validation and before analysis/stream startup."""

import asyncio
import time

from fastapi import Request


async def reserve_analysis(request: Request) -> None:
    request.state.analysis_started_at = getattr(request.state, "analysis_started_at", time.monotonic())
    await asyncio.to_thread(request.app.state.analysis_usage.reserve)
