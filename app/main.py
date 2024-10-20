from contextlib import asynccontextmanager

import aiocron
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from . import db
from .processor import sync_hn, sync_tg


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_db()  # init db
    yield
    await db.close_db()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@aiocron.crontab("*/10 * * * *")
async def sync_stories_task():
    await sync_hn()
    await sync_tg()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return {"status": "ok"}


@app.get("/stories")
async def list_stories():
    return await db.list_stories()


@app.get("/s/{story_id:int}")
async def navigate_story_url(story_id: int):
    if story_url := await db.get_story_url(story_id):
        return RedirectResponse(url=story_url)

    raise HTTPException(status_code=404)


@app.get("/c/{story_id:int}")
async def navigate_comments(story_id: int):
    return RedirectResponse(f"https://news.ycombinator.com/item?id={story_id}")
