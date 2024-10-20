import asyncio
from dataclasses import dataclass

import aiosqlite

from . import settings


@dataclass
class Story:
    id: int
    created_at: int
    title: str
    url: str | None
    score: int
    comments: int
    added_at: int
    clicks: int
    tg_msg_id: int | None


async def get_db() -> aiosqlite.Connection:
    if db := getattr(get_db, "_db", None):
        if db.is_alive:
            return db

    db = await aiosqlite.connect(settings.DB_PATH, loop=asyncio.get_event_loop())
    db.row_factory = aiosqlite.Row

    qs = """
    CREATE TABLE IF NOT EXISTS stories (
        id INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        title TEXT NOT NULL,
        url TEXT,
        score INTEGER NOT NULL,
        comments INTEGER NOT NULL,
        added_at INTEGER DEFAULT (strftime('%s', 'now')),
        clicks INTEGER DEFAULT 0,
        tg_msg_id INTEGER
    )
    """

    await db.execute(qs)
    await db.commit()

    setattr(get_db, "_db", db)
    return db


async def close_db() -> None:
    if db := getattr(get_db, "_db", None):
        await db.close()
        delattr(get_db, "_db")


# MARK: Queries


async def check_story_added(post_id: int) -> bool:
    db = await get_db()
    async with db.execute("SELECT * FROM stories WHERE id=?", (post_id,)) as cursor:
        return await cursor.fetchone() is not None


async def add_story(
    story_id: int, created_at: int, title: str, url: str | None, score: int, comments: int
) -> None:
    db = await get_db()
    qs = """
    INSERT INTO stories (id, created_at, title, url, score, comments)
    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
    """
    await db.execute(qs, (story_id, created_at, title, url, score, comments))
    await db.commit()


async def get_unposted_stories(max_count=10) -> list[Story]:
    db = await get_db()
    qs = f"""
    SELECT * FROM stories WHERE tg_msg_id IS NULL
    ORDER BY added_at ASC LIMIT {max_count}"""

    async with db.execute(qs) as cursor:
        return [Story(**dict(row)) async for row in cursor]


async def mark_posted_tg(story_id: int, tg_msg_id: int) -> None:
    db = await get_db()
    qs = """UPDATE stories SET tg_msg_id=? WHERE id=?"""
    await db.execute(qs, (tg_msg_id, story_id))
    await db.commit()


async def list_stories() -> list[Story]:
    db = await get_db()
    qs = """SELECT * FROM stories ORDER BY added_at DESC LIMIT 200"""
    async with db.execute(qs) as cursor:
        return [Story(**dict(row)) async for row in cursor]


async def get_story_url(story_id: int) -> str | None:
    db = await get_db()
    qs = """
    UPDATE stories SET clicks = clicks + 1 WHERE id = ?
    RETURNING url
    """

    async with db.execute(qs, (story_id,)) as cursor:
        if row := await cursor.fetchone():
            return row[0]

    return None
