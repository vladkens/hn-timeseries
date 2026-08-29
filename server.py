import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiocron
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from collector import DB_PATH, migrate_db, save_ranks
from collector import main as collect_stories


class RankBatch(BaseModel):
    rows: list[tuple[int, int, int]]


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


@app.post("/ranks")
def ranks(batch: RankBatch) -> dict[str, int]:
    if len(batch.rows) > 5000:
        raise HTTPException(422, "Too many rows")

    for story_id, dt, rank in batch.rows:
        if story_id <= 0 or dt % 3600 or not 1 <= rank <= 30:
            raise HTTPException(422, "Invalid rank row")

    updated = save_ranks(DB_PATH, batch.rows)
    return {"received": len(batch.rows), "updated": updated}
