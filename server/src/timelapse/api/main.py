from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from timelapse.api.images import router as images_router
from timelapse.configuration import get_settings
from timelapse.database import close_database
from timelapse.services.image_files import ensure_storage_layout


@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncIterator[None]:
    del app
    settings = get_settings()
    await asyncio.to_thread(
        ensure_storage_layout,
        settings,
    )
    try:
        yield
    finally:
        await close_database()


app = FastAPI(
    title="Time-lapse Camera API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.include_router(images_router)


@app.get(
    "/health/live",
    include_in_schema=False,
    status_code=200,
)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}
