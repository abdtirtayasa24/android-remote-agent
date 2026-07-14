from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="Time-lapse Camera API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get(
    "/health/live",
    include_in_schema=False,
    status_code=200,
)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}
