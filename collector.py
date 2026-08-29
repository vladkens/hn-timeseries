import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_CONCURRENCY = 20
HN_TIMEOUT = 10
DB_PATH = Path(os.getenv("DB_PATH", "data/hn.db"))
TARGET_SCORE = int(os.getenv("TARGET_SCORE", "150"))


@dataclass
class Story:
    id: int
    created_at: int
    title: str
    url: str | None
    score: int
    comments: int
    rank: int


async def call_hn(client: httpx.AsyncClient, method: str):
    for i in range(3):
        try:
            rep = await client.get(f"{HN_API}/{method}.json")
            rep.raise_for_status()
            return rep.json()
        except httpx.HTTPError, ValueError:
            if i == 2:
                raise
            await asyncio.sleep(2**i)


async def sync_hn() -> tuple[int, list[Story]]:
    headers = {"User-Agent": "ynews (+https://github.com/vladkens/ynews)"}

    async with httpx.AsyncClient(timeout=HN_TIMEOUT, headers=headers) as client:
        ids: list[int] = await call_hn(client, "topstories")
        now = int(time.time())
        sem = asyncio.Semaphore(HN_CONCURRENCY)

        async def fetch_story(i: int, story_id: int) -> Story:
            async with sem:
                item = await call_hn(client, f"item/{story_id}")
                return Story(
                    id=story_id,
                    created_at=item["time"],
                    title=item["title"],
                    url=item.get("url"),
                    score=item.get("score", 0),
                    comments=item.get("descendants", 0),
                    rank=i + 1,
                )

        stories = await asyncio.gather(*[fetch_story(i, x) for i, x in enumerate(ids)])
        return now, stories


def init_db(db: sqlite3.Connection) -> None:
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
    db.execute(qs)

    qs = """
    CREATE TABLE IF NOT EXISTS stories_metrics (
        id INTEGER NOT NULL,
        datetime INTEGER NOT NULL,
        score INTEGER NOT NULL,
        comments INTEGER NOT NULL,
        best_rank INTEGER CHECK(best_rank BETWEEN 1 AND 500),
        PRIMARY KEY (id, datetime)
    )
    """
    db.execute(qs)

    cols = {x[1] for x in db.execute("PRAGMA table_info(stories_metrics)")}
    if "best_rank" not in cols:
        qs = """
        ALTER TABLE stories_metrics
        ADD COLUMN best_rank INTEGER CHECK(best_rank BETWEEN 1 AND 500)
        """
        db.execute(qs)

    qs = """
    CREATE INDEX IF NOT EXISTS stories_metrics_datetime_rank_idx
    ON stories_metrics(datetime, best_rank)
    """
    db.execute(qs)


def save_stories(db_path: Path, stories: list[Story], now: int, target_score: int) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    dt = now - now % 3600

    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("PRAGMA busy_timeout = 30000")
        init_db(db)

        qs = """
        INSERT INTO stories AS t (id, created_at, title, url, score, comments, added_at)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(id) DO UPDATE SET
            score = MAX(t.score, excluded.score),
            comments = MAX(t.comments, excluded.comments)
        """
        db.executemany(
            qs,
            [(x.id, x.created_at, x.title, x.url, x.score, x.comments) for x in stories],
        )

        qs = """
        UPDATE stories SET added_at = ?
        WHERE id = ? AND added_at = 0
        """
        db.executemany(qs, [(now, x.id) for x in stories if x.score >= target_score])

        qs = """
        INSERT INTO stories_metrics AS t (id, datetime, score, comments, best_rank)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id, datetime) DO UPDATE SET
            score = MAX(t.score, excluded.score),
            comments = MAX(t.comments, excluded.comments),
            best_rank = MIN(COALESCE(t.best_rank, excluded.best_rank), excluded.best_rank)
        """
        db.executemany(qs, [(x.id, dt, x.score, x.comments, x.rank) for x in stories])


async def main() -> None:
    now, stories = await sync_hn()
    save_stories(DB_PATH, stories, now, TARGET_SCORE)
    logger.info(f"Synced {len(stories)} stories to {DB_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
