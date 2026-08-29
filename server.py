import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiocron
from fastapi import FastAPI

from collector import DB_PATH, migrate_db
from collector import main as collect_stories


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    migrate_db(DB_PATH)
    cron = aiocron.Cron("*/10 * * * *", collect_stories, loop=asyncio.get_running_loop())
    cron.start()
    try:
        yield
    finally:
        cron.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
