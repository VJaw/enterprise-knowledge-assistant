"""The §B11.3 error model: `{"error": {"code", "message", "trace_id"}}` for every non-2xx response.

`/chat` raises `ApiError` with its own trace id. Anything else that escapes a handler becomes
`INTERNAL_ERROR`, recorded as an ERROR observation in Langfuse so the response's `trace_id` leads to it.
The HTTP middleware here also feeds `/metrics`.
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import metrics
from app.observability.langfuse import current_trace_id, get_client

log = logging.getLogger("uvicorn.error")

CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
         409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR",
         504: "UPSTREAM_TIMEOUT"}


class ApiError(Exception):
    def __init__(self, code: str, status: int, message: str, trace_id: str | None = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.code, self.status, self.message = code, status, message
        self.trace_id, self.headers = trace_id, headers


def error_response(code: str, status: int, message: str, trace_id: str | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    metrics.ERRORS.inc(code)
    return JSONResponse({"error": {"code": code, "message": message, "trace_id": trace_id or None}},
                        status_code=status, headers=headers)


def _route(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return error_response(e.code, e.status, e.message, e.trace_id, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, e: RequestValidationError):
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'][1:]) or 'body'}: {err['msg']}"
                             for err in e.errors())
        return error_response("VALIDATION_ERROR", 422, problems or "Invalid request body.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        code = CODES.get(e.status_code, "HTTP_ERROR")
        return error_response(code, e.status_code, str(e.detail), headers=getattr(e, "headers", None))

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as e:  # noqa: BLE001 - the last line of defence: log, trace, structured 500
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            with get_client().start_as_current_observation(
                    name=f"error {request.method} {_route(request)}", as_type="span", level="ERROR",
                    status_message=type(e).__name__, input={"path": request.url.path}) as span:
                span.update(output={"error": type(e).__name__})
                trace_id = current_trace_id()
            response = error_response("INTERNAL_ERROR", 500,
                                      "Internal error. Quote the trace id when reporting.", trace_id)
        route = _route(request)
        metrics.HTTP_REQUESTS.inc(request.method, route, str(response.status_code))
        metrics.HTTP_LATENCY.observe(time.perf_counter() - t0, route)
        return response
