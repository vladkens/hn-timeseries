import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger

SCRAPE_PATH = Path("data/benfoxall-scrape")
SNAPSHOT_FILE = "hacker-news.html"
BACKFILL_URL = "https://ynews.fly.dev/ranks"
ITEM_PATTERN = re.compile(
    rb"""
    <tr\b
    (?=[^>]*\bclass=(?P<class_quote>["'])[^"']*\bathing\b[^"']*(?P=class_quote))
    (?=[^>]*\bid=(?P<id_quote>["'])(?P<id>\d+)(?P=id_quote))
    [^>]*>.*?
    <span\b[^>]*\bclass=(?P<rank_quote>["'])rank(?P=rank_quote)>
    (?P<rank>\d+)\.</span>
    """,
    re.DOTALL | re.VERBOSE,
)


@dataclass
class Snapshot:
    commit: str
    captured_at: int


def parse_frontpage(data: bytes) -> list[tuple[int, int]]:
    items = [(int(match["id"]), int(match["rank"])) for match in ITEM_PATTERN.finditer(data)]
    ranks = [rank for _, rank in items]
    if ranks != list(range(1, 31)):
        raise ValueError(f"Invalid HN front page ranks: {ranks}")
    if len({story_id for story_id, _ in items}) != len(items):
        raise ValueError("Duplicate story on HN front page")
    return items


def load_snapshots(repo: Path) -> list[Snapshot]:
    rep = subprocess.run(
        ["git", "log", "--reverse", "--format=%H %ct", "--", SNAPSHOT_FILE],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        Snapshot(commit, int(captured_at))
        for commit, captured_at in map(str.split, rep.stdout.splitlines())
    ]


def read_snapshots(repo: Path, snapshots: list[Snapshot]) -> Iterator[tuple[Snapshot, bytes]]:
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Unable to open git cat-file pipes")

    try:
        for snapshot in snapshots:
            proc.stdin.write(f"{snapshot.commit}:{SNAPSHOT_FILE}\n".encode())
            proc.stdin.flush()
            header = proc.stdout.readline().decode().strip().split()
            if len(header) != 3 or header[1] != "blob":
                raise RuntimeError(f"Unable to read snapshot {snapshot.commit}: {header}")
            size = int(header[2])
            data = proc.stdout.read(size)
            if len(data) != size or proc.stdout.read(1) != b"\n":
                raise RuntimeError(f"Truncated git object: {snapshot.commit}")
            yield snapshot, data
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("git cat-file failed")


def send_ranks(client: httpx.Client, ranks: dict[tuple[int, int], int]) -> tuple[int, int]:
    rows = [[story_id, dt, rank] for (story_id, dt), rank in sorted(ranks.items())]
    rep = client.post(BACKFILL_URL, json={"rows": rows})
    rep.raise_for_status()
    data = rep.json()
    return data["received"], data["updated"]


def main() -> None:
    snapshots = load_snapshots(SCRAPE_PATH)
    current_day: str | None = None
    ranks: dict[tuple[int, int], int] = {}
    parsed = 0
    skipped = 0
    received = 0
    updated = 0

    with httpx.Client(timeout=30) as client:
        for snapshot, data in read_snapshots(SCRAPE_PATH, snapshots):
            dat = datetime.fromtimestamp(snapshot.captured_at, UTC)
            day = dat.strftime("%Y-%m-%d")
            if current_day is not None and day != current_day:
                day_received, day_updated = send_ranks(client, ranks)
                received += day_received
                updated += day_updated
                ranks = {}

            current_day = day
            try:
                items = parse_frontpage(data)
            except ValueError as exc:
                skipped += 1
                logger.warning(f"Skipping {snapshot.commit} at {dat.isoformat()}: {exc}")
                continue

            dt = snapshot.captured_at - snapshot.captured_at % 3600
            for story_id, rank in items:
                key = (story_id, dt)
                ranks[key] = min(ranks.get(key, rank), rank)
            parsed += 1

        if ranks:
            day_received, day_updated = send_ranks(client, ranks)
            received += day_received
            updated += day_updated

    logger.info(
        f"Parsed {parsed} snapshots, skipped {skipped}, sent {received} ranks, "
        f"updated {updated} metrics"
    )


if __name__ == "__main__":
    main()
