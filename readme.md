# ynews

Collect the current Hacker News `topstories` list into the existing SQLite database. The collector runs once and exits; schedule it externally every ten minutes.

## Run

```bash
uv sync --locked
DB_PATH=data/hn.db uv run collector.py
```

The collector preserves the existing `stories` and `stories_metrics` tables. On first run it adds a nullable `best_rank` column to `stories_metrics`; old rows remain `NULL`, while new rows store the best rank observed during each UTC hour.

Optional environment variables:

- `DB_PATH` — SQLite path, defaults to `data/hn.db`.
- `TARGET_SCORE` — score used for the legacy `added_at` field, defaults to `150`.

## Fly.io

The Docker container runs the collector every ten minutes with Alpine's `crond`. The SQLite database is stored on the `sqlite_data` Fly volume at `/data/ynews.db`.

```bash
fly deploy --ha=false
```
