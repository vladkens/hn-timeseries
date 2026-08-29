# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "httpx>=0.28.1",
#     "loguru>=0.7.3",
# ]
# ///

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

import httpx
from loguru import logger

DATA_BRANCH = "data"
HN_API = "https://hacker-news.firebaseio.com/v0"
HN_CONCURRENCY = 20
HN_TIMEOUT = 10

logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> <level>{level.name[0]}</level> <level>{message}</level>",
    level="DEBUG",
)


@dataclass(slots=True)
class Story:
    id: int
    created_at: int
    added_at: int
    title: str
    url: str | None
    max_score: int
    max_comments: int

    def __post_init__(self) -> None:
        if self.added_at <= 0:
            raise ValueError(f"Invalid added time for story {self.id}")

    def merge(self, other: Story) -> bool:
        if self.created_at != other.created_at:
            raise ValueError(f"Conflicting creation time for story {self.id}")

        vals = (
            max(self.max_score, other.max_score),
            max(self.max_comments, other.max_comments),
            min(self.added_at, other.added_at),
        )

        changed = vals != (self.max_score, self.max_comments, self.added_at)
        self.max_score, self.max_comments, self.added_at = vals
        return changed


@dataclass(slots=True)
class Metric:
    hour: int
    id: int
    score: int
    comments: int
    best_rank: int | None

    def merge(self, other: Metric) -> None:
        self.score = max(self.score, other.score)
        self.comments = max(self.comments, other.comments)
        if other.best_rank is not None:
            self.best_rank = (
                min(self.best_rank, other.best_rank) if self.best_rank else other.best_rank
            )


@dataclass(slots=True)
class DatasetStats:
    stories: int
    metrics: int
    size_bytes: int

    @classmethod
    def load(cls, root: Path) -> DatasetStats:
        story_paths = sorted(root.glob("????-??/stories.csv"))
        metric_paths = sorted(root.glob("????-??/????-??-??.csv"))

        def count_rows(paths: list[Path]) -> int:
            count = 0
            for path in paths:
                with path.open(newline="", encoding="utf-8") as f:
                    rows = csv.reader(f)
                    next(rows, None)
                    count += sum(1 for _ in rows)

            return count

        paths = story_paths + metric_paths
        return cls(
            stories=count_rows(story_paths),
            metrics=count_rows(metric_paths),
            size_bytes=sum(path.stat().st_size for path in paths),
        )

    def save(self, path: Path) -> None:
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def report(self, before: DatasetStats) -> None:
        def change(old: int, new: int) -> str:
            return f"{old:,} -> {new:,} ({new - old:+,})"

        mib = 1024**2
        logger.info(
            f"Dataset: stories {change(before.stories, self.stories)}; "
            f"metrics {change(before.metrics, self.metrics)}; "
            f"size {before.size_bytes / mib:.1f} -> {self.size_bytes / mib:.1f} MiB "
            f"({(self.size_bytes - before.size_bytes) / mib:+.1f} MiB)"
        )


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


async def sync_hn() -> tuple[int, list[Story], list[Metric]]:
    headers = {"User-Agent": "hn-timeseries (+https://github.com/vladkens/hn-timeseries)"}

    async with httpx.AsyncClient(timeout=HN_TIMEOUT, headers=headers) as client:
        ids: list[int] = await call_hn(client, "topstories")
        now = int(time.time())
        hour = datetime.fromtimestamp(now, UTC).hour
        sem = asyncio.Semaphore(HN_CONCURRENCY)

        async def fetch_story(i: int, story_id: int) -> tuple[Story, Metric]:
            async with sem:
                item = await call_hn(client, f"item/{story_id}")
                score = item.get("score", 0)
                comments = item.get("descendants", 0)
                story = Story(
                    id=story_id,
                    created_at=item["time"],
                    added_at=now,
                    title=item["title"],
                    url=item.get("url"),
                    max_score=score,
                    max_comments=comments,
                )
                metric = Metric(hour, story_id, score, comments, i + 1)
                return story, metric

        rows = await asyncio.gather(*[fetch_story(i, story_id) for i, story_id in enumerate(ids)])
        return now, [x[0] for x in rows], [x[1] for x in rows]


@dataclass
class DataWorktree:
    path: Path
    remote: str


def get_story_path(root: Path, added_at: int) -> Path:
    month = datetime.fromtimestamp(added_at, UTC).strftime("%Y-%m")
    return root / month / "stories.csv"


def get_metric_path(root: Path, dt: int) -> Path:
    dat = datetime.fromtimestamp(dt, UTC)
    return root / dat.strftime("%Y-%m") / dat.strftime("%Y-%m-%d.csv")


def load_stories(path: Path) -> dict[int, Story]:
    if not path.exists():
        return {}

    cols = fields(Story)
    stories: dict[int, Story] = {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        if next(rows, None) != [x.name for x in cols]:
            raise ValueError(f"Invalid CSV header in {path}")

        for row in rows:
            if len(row) != len(cols):
                raise ValueError(f"Invalid story row in {path}: {row}")

            try:
                story = Story(
                    id=int(row[0]),
                    created_at=int(row[1]),
                    added_at=int(row[2]),
                    title=row[3],
                    url=row[4] or None,
                    max_score=int(row[5]),
                    max_comments=int(row[6]),
                )
            except ValueError as exc:
                raise ValueError(f"Invalid story row in {path}: {row}") from exc
            if story.id in stories:
                raise ValueError(f"Duplicate story {story.id} in {path}")

            stories[story.id] = story

    return stories


def load_metrics(path: Path) -> dict[tuple[int, int], Metric]:
    if not path.exists():
        return {}

    cols = fields(Metric)
    metrics: dict[tuple[int, int], Metric] = {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        if next(rows, None) != [x.name for x in cols]:
            raise ValueError(f"Invalid CSV header in {path}")

        for row in rows:
            if len(row) != len(cols):
                raise ValueError(f"Invalid metric row in {path}: {row}")

            try:
                metric = Metric(
                    hour=int(row[0]),
                    id=int(row[1]),
                    score=int(row[2]),
                    comments=int(row[3]),
                    best_rank=int(row[4]) if row[4] else None,
                )
            except ValueError as exc:
                raise ValueError(f"Invalid metric row in {path}: {row}") from exc
            if not 0 <= metric.hour <= 23:
                raise ValueError(f"Invalid metric hour in {path}: {metric.hour}")
            if metric.best_rank is not None and not 1 <= metric.best_rank <= 500:
                raise ValueError(f"Invalid rank in {path}: {metric.best_rank}")

            key = (metric.hour, metric.id)
            if key in metrics:
                raise ValueError(f"Duplicate metric {key} in {path}")

            metrics[key] = metric

    return metrics


def write_stories(path: Path, rows: list[Story]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    cols = fields(Story)
    with tmp.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(x.name for x in cols)
        rows = sorted(rows, key=lambda row: row.id)
        out.writerows((getattr(row, x.name) for x in cols) for row in rows)
    tmp.replace(path)


def write_metrics(path: Path, rows: list[Metric]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(x.name for x in fields(Metric))
        rows = sorted(rows, key=lambda row: (row.hour, row.id))
        out.writerows((row.hour, row.id, row.score, row.comments, row.best_rank) for row in rows)
    tmp.replace(path)


def save_stories(root: Path, stories: list[Story]) -> list[Path]:
    locations = {
        story_id: path
        for path in root.glob("????-??/stories.csv")
        for story_id in load_stories(path)
    }
    grouped: dict[Path, list[Story]] = defaultdict(list)
    for story in stories:
        path = locations.get(story.id, get_story_path(root, story.added_at))
        grouped[path].append(story)

    paths: list[Path] = []
    for path, items in sorted(grouped.items()):
        rows = load_stories(path)
        changed = False

        for story in items:
            if row := rows.get(story.id):
                changed |= row.merge(story)
            else:
                rows[story.id] = story
                changed = True

        if changed:
            write_stories(path, list(rows.values()))
        paths.append(path)

    return paths


def save_metrics(path: Path, metrics: list[Metric]) -> None:
    rows = load_metrics(path)

    for metric in metrics:
        key = (metric.hour, metric.id)
        if current := rows.get(key):
            current.merge(metric)
        else:
            rows[key] = metric

    write_metrics(path, list(rows.values()))


def save_data(root: Path, stories: list[Story], metrics: list[Metric], now: int) -> None:
    dt = now - now % 3600
    save_stories(root, stories)
    metric_path = get_metric_path(root, dt)
    save_metrics(metric_path, metrics)
    dat = datetime.fromtimestamp(dt, UTC)
    logger.info(
        f"Collected {len(stories)} stories and {len(metrics)} metrics for {dat:%F %H}:00 UTC"
    )


def check_db(db: sqlite3.Connection) -> None:
    row = db.execute("PRAGMA quick_check").fetchone()
    if row != ("ok",):
        raise RuntimeError(f"SQLite quick_check failed: {row}")


def init_db(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            score INTEGER NOT NULL,
            comments INTEGER NOT NULL,
            added_at INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS stories_metrics (
            id INTEGER NOT NULL,
            datetime INTEGER NOT NULL,
            score INTEGER NOT NULL,
            comments INTEGER NOT NULL,
            best_rank INTEGER CHECK(best_rank BETWEEN 1 AND 500),
            PRIMARY KEY (id, datetime)
        )
        """
    )


def save_sql(path: Path, stories: list[Story], metrics: list[Metric], now: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dt = now - now % 3600

    with sqlite3.connect(path, timeout=30) as db:
        db.execute("PRAGMA busy_timeout = 30000")
        init_db(db)
        db.executemany(
            """
            INSERT INTO stories AS t (id, created_at, title, url, score, comments, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                score = MAX(t.score, excluded.score),
                comments = MAX(t.comments, excluded.comments),
                added_at = CASE
                    WHEN t.added_at > 0 THEN MIN(t.added_at, excluded.added_at)
                    ELSE excluded.added_at
                END
            """,
            [
                (
                    story.id,
                    story.created_at,
                    story.title,
                    story.url,
                    story.max_score,
                    story.max_comments,
                    story.added_at,
                )
                for story in stories
            ],
        )
        db.executemany(
            """
            INSERT INTO stories_metrics AS t (id, datetime, score, comments, best_rank)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id, datetime) DO UPDATE SET
                score = MAX(t.score, excluded.score),
                comments = MAX(t.comments, excluded.comments),
                best_rank = MIN(COALESCE(t.best_rank, excluded.best_rank), excluded.best_rank)
            """,
            [
                (metric.id, dt, metric.score, metric.comments, metric.best_rank)
                for metric in metrics
            ],
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS stories_metrics_datetime_rank_idx
            ON stories_metrics(datetime, best_rank)
            """
        )

    dat = datetime.fromtimestamp(dt, UTC)
    logger.info(f"Saved {len(stories)} stories and {len(metrics)} metrics for {dat:%F %H}:00 UTC")


async def serve(path: Path) -> None:
    while True:
        try:
            now, stories, metrics = await sync_hn()
            save_sql(path, stories, metrics, now)
        except httpx.HTTPError, sqlite3.Error, OSError, ValueError:
            logger.exception("Collection failed")

        now = time.time()
        delay = 15 * 60 - (now - 11 * 60) % (15 * 60)
        next_run = datetime.fromtimestamp(now + delay, UTC)
        logger.info(f"Next run at {next_run:%F %H:%M} UTC")
        await asyncio.sleep(delay)


def metric_query(db: sqlite3.Connection) -> str:
    cols = {row[1] for row in db.execute("PRAGMA table_info(stories_metrics)")}
    rank = "best_rank" if "best_rank" in cols else "NULL"
    return f"""
        SELECT id, datetime, score, comments, {rank}
        FROM stories_metrics
        WHERE datetime >= ?
        ORDER BY datetime, id
    """


def merge_stories(path: Path, items: list[Story]) -> None:
    rows = load_stories(path)
    for story in items:
        if current := rows.get(story.id):
            current.merge(story)
        else:
            rows[story.id] = story
    write_stories(path, list(rows.values()))


def merge_metrics(path: Path, items: list[Metric]) -> int:
    rows = load_metrics(path)
    before = len(rows)
    for metric in items:
        key = (metric.hour, metric.id)
        if current := rows.get(key):
            current.merge(metric)
        else:
            rows[key] = metric
    write_metrics(path, list(rows.values()))
    return len(rows) - before


def export_files(
    db: sqlite3.Connection,
    worktree: DataWorktree,
    start_at: int,
) -> DatasetStats:
    target = worktree.path / "data"
    stories: dict[int, Story] = {}
    known_story_ids = {
        story_id for path in target.glob("????-??/stories.csv") for story_id in load_stories(path)
    }
    stats = DatasetStats.load(target)

    ADDED_AT_FIXED_AT = int(datetime(2026, 8, 29, 19, tzinfo=UTC).timestamp())

    rows = db.execute(
        """
        WITH first_seen AS (
            SELECT id, MIN(datetime) AS added_at FROM stories_metrics GROUP BY id
        )
        SELECT
            s.id,
            s.created_at,
            CASE WHEN f.added_at < ? THEN f.added_at ELSE s.added_at END,
            s.title,
            s.url,
            s.score,
            s.comments
        FROM stories s
        JOIN first_seen f ON f.id = s.id
        ORDER BY s.id
        """,
        (ADDED_AT_FIXED_AT,),
    )
    for row in rows:
        story = Story(*row)
        stories[story.id] = story

    changed = False
    metric_rows = db.execute(metric_query(db), (start_at,))
    for path, day_rows in groupby(
        metric_rows,
        key=lambda row: get_metric_path(target, row[1]),
    ):
        metrics: list[Metric] = []
        current_at = start_at
        for story_id, dt, score, comments, best_rank in day_rows:
            if story_id not in stories:
                continue
            if dt % 3600:
                raise RuntimeError(f"Metric {story_id} has non-hour timestamp {dt}")

            current_at = dt
            hour = datetime.fromtimestamp(dt, UTC).hour
            metrics.append(Metric(hour, story_id, score, comments, best_rank))

        day_story_ids = {metric.id for metric in metrics}
        save_stories(target, [stories[story_id] for story_id in day_story_ids])
        stats.stories += len(day_story_ids - known_story_ids)
        known_story_ids.update(day_story_ids)
        stats.metrics += merge_metrics(path, metrics)
        stats.size_bytes = sum(path.stat().st_size for path in target.glob("????-??/*.csv"))
        stats.save(worktree.path / "stats.json")

        if commit_data(worktree, current_at):
            dat = datetime.fromtimestamp(current_at, UTC)
            logger.info(
                f"Merged {len(day_story_ids)} stories and {len(metrics)} metrics for {dat:%F}"
            )
            changed = True

    if not changed:
        dat = datetime.fromtimestamp(start_at, UTC)
        logger.info(f"No new data after {dat:%F %H}:00 UTC")

    return stats


def sql_to_files(source: Path, worktree: DataWorktree) -> DatasetStats:
    if not source.is_file():
        raise FileNotFoundError(source)

    target = worktree.path / "data"
    if target.exists() and not target.is_dir():
        raise NotADirectoryError(target)

    target.mkdir(parents=True, exist_ok=True)
    start_at = 0
    if paths := sorted(target.glob("????-??/????-??-??.csv")):
        last_path = paths[-1]
        metrics = load_metrics(last_path)
        if metrics:
            start_at = parse_metric_date(last_path) + max(hour for hour, _ in metrics) * 3600

    uri = f"{source.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only = ON")
        check_db(db)
        row = db.execute("SELECT MAX(datetime) FROM stories_metrics").fetchone()
        source_at = row[0]
        if source_at is None:
            raise ValueError("SQLite dump contains no metrics")
        if start_at > source_at:
            source_dat = datetime.fromtimestamp(source_at, UTC)
            current_dat = datetime.fromtimestamp(start_at, UTC)
            raise ValueError(
                f"SQLite dump ends at {source_dat:%F %H}:00 UTC, "
                f"dataset already contains data through {current_dat:%F %H}:00 UTC"
            )

        return export_files(db, worktree, start_at)


def parse_metric_date(path: Path) -> int:
    month = path.parent.name
    if path.stem[:7] != month:
        raise ValueError(f"Metric path has mismatched month: {path}")
    return int(datetime.strptime(path.stem, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def import_stories(db: sqlite3.Connection, paths: list[Path]) -> tuple[int, set[int]]:
    count = 0
    story_ids: set[int] = set()
    qs = """
        INSERT INTO stories (id, created_at, added_at, title, url, score, comments)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """

    for path in paths:
        rows = load_stories(path)
        vals: list[tuple[object, ...]] = []
        for story in rows.values():
            month = datetime.fromtimestamp(story.added_at, UTC).strftime("%Y-%m")
            if month != path.parent.name:
                raise ValueError(f"Story {story.id} is stored in the wrong month: {path}")
            if story.id in story_ids:
                raise ValueError(f"Duplicate story {story.id} across dataset")

            story_ids.add(story.id)
            vals.append(
                (
                    story.id,
                    story.created_at,
                    story.added_at,
                    story.title,
                    story.url,
                    story.max_score,
                    story.max_comments,
                )
            )

        db.executemany(qs, vals)
        count += len(vals)

    return count, story_ids


def import_metrics(db: sqlite3.Connection, paths: list[Path], story_ids: set[int]) -> int:
    count = 0
    qs = """
        INSERT INTO stories_metrics (id, datetime, score, comments, best_rank)
        VALUES (?, ?, ?, ?, ?)
    """

    for path in paths:
        day = parse_metric_date(path)
        rows = load_metrics(path)
        vals: list[tuple[object, ...]] = []
        for metric in rows.values():
            if metric.id not in story_ids:
                raise ValueError(f"Metric references missing story {metric.id}: {path}")

            vals.append(
                (
                    metric.id,
                    day + metric.hour * 3600,
                    metric.score,
                    metric.comments,
                    metric.best_rank,
                )
            )

        db.executemany(qs, vals)
        count += len(vals)

    return count


def files_to_sql(source: Path, target: Path) -> None:
    story_paths = sorted(source.glob("????-??/stories.csv"))
    metric_paths = sorted(source.glob("????-??/????-??-??.csv"))
    if not story_paths:
        raise FileNotFoundError(f"No dataset found in {source}")
    if target.exists():
        raise FileExistsError(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    try:
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        init_db(db)
        story_count, story_ids = import_stories(db, story_paths)
        metric_count = import_metrics(db, metric_paths, story_ids)
        db.execute(
            """
            CREATE INDEX stories_metrics_datetime_rank_idx
            ON stories_metrics(datetime, best_rank)
            """
        )
        db.commit()
        db.execute("PRAGMA journal_mode = DELETE")
        check_db(db)
    except Exception:
        db.close()
        target.unlink(missing_ok=True)
        raise
    else:
        db.close()

    logger.info(f"Imported {story_count} stories and {metric_count} metrics to {target}")


def run_git(
    repo: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    rep = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    if check and rep.returncode != 0:
        raise RuntimeError(rep.stderr.strip())
    return rep


def git_output(repo: Path, *args: str) -> str:
    rep = run_git(repo, *args)
    return rep.stdout.strip()


def get_remote(repo: Path) -> str:
    remotes = git_output(repo, "remote").splitlines()
    for remote in ("upstream", "origin"):
        if remote in remotes:
            return remote
    raise RuntimeError("Git remote not found")


def has_remote_branch(repo: Path, remote: str) -> bool:
    rep = run_git(
        repo,
        "ls-remote",
        "--heads",
        remote,
        f"refs/heads/{DATA_BRANCH}",
    )
    return bool(rep.stdout.strip())


def has_local_branch(repo: Path) -> bool:
    rep = run_git(
        repo,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{DATA_BRANCH}",
        check=False,
    )
    return rep.returncode == 0


def create_worktree(repo: Path, path: Path) -> DataWorktree:
    if path.exists():
        raise FileExistsError(f"Worktree already exists after a failed run: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    run_git(repo, "worktree", "prune")
    remote = get_remote(repo)

    remote_ref: str | None = None
    if has_remote_branch(repo, remote):
        remote_ref = f"refs/remotes/{remote}/{DATA_BRANCH}"
        run_git(repo, "fetch", remote, f"refs/heads/{DATA_BRANCH}:{remote_ref}")

    if has_local_branch(repo):
        run_git(repo, "worktree", "add", str(path), DATA_BRANCH)
        if remote_ref is not None:
            run_git(path, "merge", "--ff-only", remote_ref)
        return DataWorktree(path, remote)

    if remote_ref is not None:
        run_git(repo, "worktree", "add", "-b", DATA_BRANCH, str(path), remote_ref)
        return DataWorktree(path, remote)

    run_git(repo, "worktree", "add", "--orphan", "-b", DATA_BRANCH, str(path))
    return DataWorktree(path, remote)


def commit_git(worktree: Path, *args: str, commit_at: int | None = None) -> None:
    env = None
    if commit_at is not None:
        timestamp = datetime.fromtimestamp(commit_at, UTC).isoformat()
        env = os.environ | {"GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp}

    uname = "user.name=github-actions[bot]"
    umail = "user.email=41898282+github-actions[bot]@users.noreply.github.com"
    run_git(worktree, "-c", uname, "-c", umail, "commit", *args, env=env)


def commit_data(worktree: DataWorktree, commit_at: int | None = None) -> bool:
    run_git(worktree.path, "add", "data")
    if (worktree.path / "stats.json").exists():
        run_git(worktree.path, "add", "stats.json")

    diff = run_git(worktree.path, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return False
    if diff.returncode != 1:
        raise RuntimeError("Unable to inspect staged dataset changes")

    now = datetime.fromtimestamp(commit_at, UTC) if commit_at is not None else datetime.now(UTC)
    message = f"data {now:%Y-%m-%d}"
    head = run_git(
        worktree.path,
        "rev-parse",
        "--verify",
        "HEAD",
        check=False,
    )
    amend = (
        head.returncode == 0 and git_output(worktree.path, "log", "-1", "--format=%s") == message
    )

    args = ("--amend", "--no-edit", "--reset-author") if amend else ("-m", message)
    commit_git(worktree.path, *args, commit_at=commit_at)

    return True


@contextmanager
def open_worktree(repo: Path, path: Path) -> Generator[DataWorktree]:
    worktree = create_worktree(repo, path)
    try:
        yield worktree
        commit_data(worktree)
    finally:
        run_git(repo, "worktree", "remove", "--force", str(path))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the Git-backed Hacker News dataset")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect", help="collect HN and update the data branch")
    serve_parser = commands.add_parser("serve", help="continuously collect HN into SQLite")
    serve_parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        default=Path("data/ynews.db"),
        help="SQLite database to update",
    )
    import_parser = commands.add_parser("import", help="merge SQLite into the data branch")
    import_parser.add_argument("source", type=Path, help="SQLite database to merge")
    export_parser = commands.add_parser("export", help="build SQLite from the data branch")
    export_parser.add_argument("target", type=Path, help="new SQLite database to create")
    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(serve(args.target.resolve()))
        return

    repo = Path(__file__).resolve().parent
    with open_worktree(repo, repo / ".worktree") as worktree:
        data_path = worktree.path / "data"
        match args.command:
            case "collect":
                now, stories, metrics = asyncio.run(sync_hn())
                before = DatasetStats.load(data_path)
                save_data(data_path, stories, metrics, now)
                after = DatasetStats.load(data_path)
                after.save(worktree.path / "stats.json")
            case "import":
                before = DatasetStats.load(data_path)
                after = sql_to_files(args.source.resolve(), worktree)
            case "export":
                files_to_sql(data_path, args.target.resolve())
                return

        after.report(before)


if __name__ == "__main__":
    main()
