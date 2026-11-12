"""POST analysis JSON/SSE transport with one terminal event and a hard response budget."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import json
from typing import Any
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from benefitfinder.api.schemas import ErrorResponse
from benefitfinder.application.recommendation_flow import Progress

Analysis = Callable[[Progress], Awaitable[dict[str, Any]]]


def wants_event_stream(accept: str) -> bool:
    for entry in accept.split(","):
        media, *parameters = entry.strip().lower().split(";")
        if media != "text/event-stream":
            continue
        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.strip().partition("=")
            if name == "q" and separator:
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if 0 < quality <= 1:
            return True
    return False


async def public_failure(request: Request, error: Exception) -> Response:
    # Reuse the registered HTTP/domain handlers so JSON and SSE have identical safe errors.
    for kind in type(error).__mro__:
        handler = request.app.exception_handlers.get(kind)
        if handler is not None:
            try:
                response = await handler(request, error)
                ErrorResponse.model_validate_json(response.body)
                return response
            except Exception:
                break
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            request_id=str(uuid.uuid4()),
            code="internal_error",
            message="내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        ).model_dump(),
    )


def frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n"


async def analysis_response(
    request: Request,
    analyze: Analysis,
    *,
    timeout_seconds: float,
    stream: bool,
) -> Response:
    async def run(progress: Progress) -> dict[str, Any]:
        deadline = asyncio.timeout(timeout_seconds)
        try:
            async with deadline:
                return await analyze(progress)
        except TimeoutError:
            if not deadline.expired():
                raise
            raise HTTPException(status_code=503, detail="calculation_limit_exceeded") from None

    if not stream:
        try:
            return JSONResponse(await run(lambda _: None))
        except Exception as error:
            return await public_failure(request, error)

    async def events():
        queue: asyncio.Queue[str] = asyncio.Queue()
        current: dict[str, str] | None = None

        def progress(value: dict[str, str]) -> None:
            nonlocal current
            current = value if value["state"] == "running" else None
            queue.put_nowait(frame("progress", value))

        async def produce() -> None:
            try:
                result = await run(progress)
                queue.put_nowait(frame("result", result))
            except Exception as error:
                if current is not None:
                    queue.put_nowait(frame("progress", {**current, "state": "failed"}))
                response = await public_failure(request, error)
                queue.put_nowait(frame("error", json.loads(response.body)))

        task = asyncio.create_task(produce())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield item
                if item.startswith(("event: result\n", "event: error\n")):
                    return
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
