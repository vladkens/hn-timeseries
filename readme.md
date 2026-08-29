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

## Git dataset

```bash
uv run dataset.py collect
```

The Git collector writes each story to the UTC month containing its `created_at` timestamp at `data/<year>-<month>/_stories.csv`. Hourly aggregates are stored in daily files at `data/<year>-<month>/<year>-<month>-<day>.csv`. Repeated runs within the hour preserve every observed story and store the maximum score, maximum comment count, and best rank. Rows are sorted by UTC hour and story ID so Git diffs stay stable.

Story files contain `id,created_at,added_at,title,url,max_score,max_comments`. Metric files contain `hour,id,score,comments,best_rank`; the filename contains their UTC date. Empty `added_at` and `best_rank` fields mean that the value is unknown.

Merge an SQLite dump into the file dataset:

```bash
uv run dataset.py import data/ynews.db
```

The operation is idempotent and may be repeated with a newer dump. Existing rows keep the maximum score and comment count, the earliest known `added_at`, and the best rank.

Build a clean analytical SQLite database from the files:

```bash
uv run dataset.py export data/ynews-restored.db
```

All commands create a temporary linked worktree at `~/.worktrees/ynews--data`. `collect` and `import` update and commit the local `data` branch but never push it; `export` only reads the branch. A successful command removes its worktree, while a failed command preserves it for inspection. Push a completed local checkpoint with `git push --force-with-lease upstream data`.

## GitHub Actions

The [`collect`](.github/workflows/collect.yml) workflow runs `uv run dataset.py collect` every ten minutes. The same command is used locally and in CI for worktree creation, collection, commit, and cleanup; CI performs its push in a separate step.

All runs within the same UTC day amend one commit. This persists every successful collection while keeping the visible history to one commit per day.

## Fly.io

The FastAPI process runs the collector every ten minutes. The SQLite database is stored on the `sqlite_data` Fly volume at `/data/ynews.db`.

```bash
fly deploy --ha=false
```
