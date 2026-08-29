import asyncio
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from collector import TARGET_SCORE, Story, sync_hn

STORY_HEADER = [
    "id",
    "created_at",
    "added_at",
    "title",
    "url",
    "max_score",
    "max_comments",
]
METRIC_HEADER = ["hour", "id", "score", "comments", "best_rank"]


@dataclass
class StoryRow:
    id: int
    created_at: int
    added_at: int | None
    title: str
    url: str | None
    max_score: int
    max_comments: int

    @classmethod
    def from_story(cls, story: Story, now: int, target_score: int) -> StoryRow:
        added_at = now if story.score >= target_score else None
        return cls(
            story.id,
            story.created_at,
            added_at,
            story.title,
            story.url,
            story.score,
            story.comments,
        )

    def update(self, story: Story, now: int, target_score: int) -> bool:
        max_score = max(self.max_score, story.score)
        max_comments = max(self.max_comments, story.comments)
        added_at = self.added_at
        if added_at is None and story.score >= target_score:
            added_at = now

        changed = (max_score, max_comments, added_at) != (
            self.max_score,
            self.max_comments,
            self.added_at,
        )
        self.max_score = max_score
        self.max_comments = max_comments
        self.added_at = added_at
        return changed

    def merge(self, other: StoryRow) -> None:
        if self.created_at != other.created_at:
            raise ValueError(f"Conflicting creation time for story {self.id}")

        self.max_score = max(self.max_score, other.max_score)
        self.max_comments = max(self.max_comments, other.max_comments)
        if other.added_at is not None:
            self.added_at = (
                other.added_at if self.added_at is None else min(self.added_at, other.added_at)
            )


@dataclass
class MetricRow:
    hour: int
    id: int
    score: int
    comments: int
    best_rank: int | None

    @classmethod
    def from_story(cls, hour: int, story: Story) -> MetricRow:
        return cls(hour, story.id, story.score, story.comments, story.rank)

    def update(self, story: Story) -> None:
        self.score = max(self.score, story.score)
        self.comments = max(self.comments, story.comments)
        self.best_rank = story.rank if self.best_rank is None else min(self.best_rank, story.rank)

    def merge(self, other: MetricRow) -> None:
        self.score = max(self.score, other.score)
        self.comments = max(self.comments, other.comments)
        if other.best_rank is not None:
            self.best_rank = (
                other.best_rank if self.best_rank is None else min(self.best_rank, other.best_rank)
            )


def get_story_path(root: Path, created_at: int) -> Path:
    month = datetime.fromtimestamp(created_at, UTC).strftime("%Y-%m")
    return root / month / "_stories.csv"


def get_metric_path(root: Path, dt: int) -> Path:
    dat = datetime.fromtimestamp(dt, UTC)
    return root / dat.strftime("%Y-%m") / dat.strftime("%Y-%m-%d.csv")


def load_stories(path: Path) -> dict[int, StoryRow]:
    if not path.exists():
        return {}

    stories: dict[int, StoryRow] = {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        if next(rows, None) != STORY_HEADER:
            raise ValueError(f"Invalid CSV header in {path}")

        for row in rows:
            if len(row) != len(STORY_HEADER):
                raise ValueError(f"Invalid story row in {path}: {row}")
            story = StoryRow(
                int(row[0]),
                int(row[1]),
                int(row[2]) if row[2] else None,
                row[3],
                row[4] or None,
                int(row[5]),
                int(row[6]),
            )
            if story.id in stories:
                raise ValueError(f"Duplicate story {story.id} in {path}")
            stories[story.id] = story

    return stories


def load_metrics(path: Path) -> dict[tuple[int, int], MetricRow]:
    if not path.exists():
        return {}

    metrics: dict[tuple[int, int], MetricRow] = {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        if next(rows, None) != METRIC_HEADER:
            raise ValueError(f"Invalid CSV header in {path}")

        for row in rows:
            if len(row) != len(METRIC_HEADER):
                raise ValueError(f"Invalid metric row in {path}: {row}")
            metric = MetricRow(
                int(row[0]),
                int(row[1]),
                int(row[2]),
                int(row[3]),
                int(row[4]) if row[4] else None,
            )
            if not 0 <= metric.hour <= 23:
                raise ValueError(f"Invalid metric hour in {path}: {metric.hour}")
            if metric.best_rank is not None and not 1 <= metric.best_rank <= 500:
                raise ValueError(f"Invalid rank in {path}: {metric.best_rank}")
            key = (metric.hour, metric.id)
            if key in metrics:
                raise ValueError(f"Duplicate metric {key} in {path}")
            metrics[key] = metric

    return metrics


def write_stories(path: Path, rows: list[StoryRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(STORY_HEADER)
        out.writerows(
            (
                x.id,
                x.created_at,
                x.added_at,
                x.title,
                x.url or "",
                x.max_score,
                x.max_comments,
            )
            for x in sorted(rows, key=lambda x: x.id)
        )
    tmp.replace(path)


def write_metrics(path: Path, rows: list[MetricRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f, lineterminator="\n")
        out.writerow(METRIC_HEADER)
        out.writerows(
            (x.hour, x.id, x.score, x.comments, x.best_rank)
            for x in sorted(rows, key=lambda x: (x.hour, x.id))
        )
    tmp.replace(path)


def save_stories(root: Path, stories: list[Story], now: int, target_score: int) -> list[Path]:
    grouped: dict[Path, list[Story]] = defaultdict(list)
    for story in stories:
        grouped[get_story_path(root, story.created_at)].append(story)

    paths: list[Path] = []
    for path, items in sorted(grouped.items()):
        rows = load_stories(path)
        changed = False

        for story in items:
            if row := rows.get(story.id):
                changed |= row.update(story, now, target_score)
            else:
                rows[story.id] = StoryRow.from_story(story, now, target_score)
                changed = True

        if changed:
            write_stories(path, list(rows.values()))
        paths.append(path)

    return paths


def save_metrics(path: Path, hour: int, stories: list[Story]) -> None:
    rows = load_metrics(path)

    for story in stories:
        key = (hour, story.id)
        if metric := rows.get(key):
            metric.update(story)
        else:
            rows[key] = MetricRow.from_story(hour, story)

    write_metrics(path, list(rows.values()))


def save_data(root: Path, stories: list[Story], now: int) -> tuple[list[Path], Path]:
    dt = now - now % 3600
    story_paths = save_stories(root, stories, now, TARGET_SCORE)
    metric_path = get_metric_path(root, dt)
    hour = datetime.fromtimestamp(dt, UTC).hour
    save_metrics(metric_path, hour, stories)
    return story_paths, metric_path


async def main() -> None:
    now, stories = await sync_hn()
    story_paths, metric_path = save_data(Path("data"), stories, now)
    logger.info(
        f"Saved {len(stories)} stories across {len(story_paths)} metadata files and {metric_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
