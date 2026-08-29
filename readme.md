# ynews

Collect the current Hacker News `topstories` list into the existing SQLite database. `collector.py` performs a single collection, while the deployed FastAPI service runs it every ten minutes.

## Run

```bash
uv sync --locked
DB_PATH=data/hn.db uv run collector.py
```

The collector preserves the existing `stories` and `stories_metrics` tables. On first run it adds a nullable `best_rank` column to `stories_metrics`; old rows remain `NULL`, while new rows store the best rank observed during each UTC hour.

On startup the service also removes the obsolete `clicks` and `tg_msg_id` columns from `stories`.

Optional environment variables:

- `DB_PATH` — SQLite path, defaults to `data/hn.db`.
- `TARGET_SCORE` — score used for the legacy `added_at` field, defaults to `150`.

## Fly.io

The FastAPI process runs the collector every ten minutes. The SQLite database is stored on the `sqlite_data` Fly volume at `/data/ynews.db`.

```bash
fly deploy --ha=false
```

To backfill historical top-30 ranks through the temporary public API, run:

```bash
uv run backfill.py
```

The importer is idempotent: it only improves `best_rank` on existing hourly metric rows and never creates synthetic metrics.
