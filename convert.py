import argparse
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from collect_csv import (
    MetricRow,
    StoryRow,
    get_metric_path,
    get_story_path,
    load_metrics,
    load_stories,
    write_metrics,
    write_stories,
)


def check_db(db: sqlite3.Connection) -> None:
    row = db.execute("PRAGMA quick_check").fetchone()
    if row != ("ok",):
        raise RuntimeError(f"SQLite quick_check failed: {row}")


def init_db(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE stories (
            id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            score INTEGER NOT NULL,
            comments INTEGER NOT NULL,
            added_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        """
        CREATE TABLE stories_metrics (
            id INTEGER NOT NULL,
            datetime INTEGER NOT NULL,
            score INTEGER NOT NULL,
            comments INTEGER NOT NULL,
            best_rank INTEGER CHECK(best_rank BETWEEN 1 AND 500),
            PRIMARY KEY (id, datetime)
        )
        """
    )


def metric_query(db: sqlite3.Connection) -> str:
    cols = {x[1] for x in db.execute("PRAGMA table_info(stories_metrics)")}
    rank = "best_rank" if "best_rank" in cols else "NULL"
    return f"""
        SELECT id, datetime, score, comments, {rank}
        FROM stories_metrics
        ORDER BY datetime, id
    """


def merge_stories(path: Path, items: list[StoryRow]) -> None:
    rows = load_stories(path)
    for story in items:
        if current := rows.get(story.id):
            current.merge(story)
        else:
            rows[story.id] = story
    write_stories(path, list(rows.values()))


def merge_metrics(path: Path, items: list[MetricRow]) -> None:
    rows = load_metrics(path)
    for metric in items:
        key = (metric.hour, metric.id)
        if current := rows.get(key):
            current.merge(metric)
        else:
            rows[key] = metric
    write_metrics(path, list(rows.values()))


def export_files(db: sqlite3.Connection, target: Path) -> tuple[int, int, int]:
    grouped: dict[Path, list[StoryRow]] = defaultdict(list)
    story_ids: set[int] = set()

    rows = db.execute(
        """
        SELECT id, created_at, added_at, title, url, score, comments
        FROM stories
        ORDER BY id
        """
    )
    for row in rows:
        story = StoryRow(
            id=row[0],
            created_at=row[1],
            added_at=row[2] or None,
            title=row[3],
            url=row[4],
            max_score=row[5],
            max_comments=row[6],
        )
        story_ids.add(story.id)
        grouped[get_story_path(target, story.created_at)].append(story)

    for path, stories in sorted(grouped.items()):
        merge_stories(path, stories)

    metrics: list[MetricRow] = []
    current_path: Path | None = None
    metric_count = 0
    day_count = 0

    for row in db.execute(metric_query(db)):
        story_id, dt, score, comments, best_rank = row
        if story_id not in story_ids:
            raise RuntimeError(f"Metric references missing story {story_id}")
        if dt % 3600:
            raise RuntimeError(f"Metric {story_id} has non-hour timestamp {dt}")

        path = get_metric_path(target, dt)
        if current_path is not None and path != current_path:
            merge_metrics(current_path, metrics)
            metrics = []
            day_count += 1
            if day_count % 100 == 0:
                logger.info(f"Merged {day_count} daily metric files")

        current_path = path
        hour = datetime.fromtimestamp(dt, UTC).hour
        metrics.append(MetricRow(hour, story_id, score, comments, best_rank))
        metric_count += 1

    if current_path is not None:
        merge_metrics(current_path, metrics)
        day_count += 1

    return len(story_ids), metric_count, day_count


def sql_to_files(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists() and not target.is_dir():
        raise NotADirectoryError(target)

    target.mkdir(parents=True, exist_ok=True)
    uri = f"{source.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only = ON")
        check_db(db)
        story_count, metric_count, day_count = export_files(db, target)

    logger.info(
        f"Merged {story_count} stories and {metric_count} metrics "
        f"across {day_count} days into {target}"
    )


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
        vals: list[tuple[int, int, int, str, str | None, int, int]] = []
        for story in rows.values():
            month = datetime.fromtimestamp(story.created_at, UTC).strftime("%Y-%m")
            if month != path.parent.name:
                raise ValueError(f"Story {story.id} is stored in the wrong month: {path}")
            if story.id in story_ids:
                raise ValueError(f"Duplicate story {story.id} across dataset")
            story_ids.add(story.id)
            vals.append(
                (
                    story.id,
                    story.created_at,
                    story.added_at or 0,
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
        vals: list[tuple[int, int, int, int, int | None]] = []
        for metric in rows.values():
            if metric.id not in story_ids:
                raise ValueError(f"Metric references missing story {metric.id}: {path}")
            dt = day + metric.hour * 3600
            vals.append((metric.id, dt, metric.score, metric.comments, metric.best_rank))

        db.executemany(qs, vals)
        count += len(vals)

    return count


def files_to_sql(source: Path, target: Path) -> None:
    story_paths = sorted(source.glob("????-??/_stories.csv"))
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["sql-to-files", "files-to-sql"])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()

    if args.mode == "sql-to-files":
        sql_to_files(args.source, args.target)
    else:
        files_to_sql(args.source, args.target)


if __name__ == "__main__":
    main()
