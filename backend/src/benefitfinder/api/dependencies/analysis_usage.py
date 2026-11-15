"""Reserve once after HTTP input validation and before analysis/stream startup."""

import asyncio

from fastapi import Request

from benefitfinder.api.analysis_response import within_analysis_budget


async def reserve_analysis(request: Request) -> None:
    await within_analysis_budget(
        request, lambda: asyncio.to_thread(request.app.state.analysis_usage.reserve),
    )
