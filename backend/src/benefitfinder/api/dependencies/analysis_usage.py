"""Reserve analysis usage only after acquiring a shared request slot."""


from fastapi import Request

from benefitfinder.api.analysis_response import within_analysis_budget
from benefitfinder.application.analysis_concurrency import AnalysisConcurrencyBusy


async def reserve_analysis(request: Request) -> None:
    guard = request.app.state.analysis_concurrency

    try:
        lease = guard.acquire_request()
    except AnalysisConcurrencyBusy:
        raise AnalysisConcurrencyBusy("analysis_busy") from None

    request.state.analysis_lease = lease

    try:
        await within_analysis_budget(
            request,
            lambda: lease.run_in_thread(request.app.state.analysis_usage.reserve),
        )
    except BaseException:
        lease.close()
        request.state.analysis_lease = None
        raise
