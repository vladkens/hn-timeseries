# hn-timeseries

Like many people in tech, I read Hacker News. I have always been curious about how submissions reach the top: how quickly they climb, how long they remain visible, and what the ranking algorithm looks like from the outside.

A few years ago, I wrote a small scraper to collect enough data to analyze this. Then I mostly forgot about it, while the collector quietly continued running and gathering data from November 2024 onward.

I recently rediscovered it and wondered whether I should simply shut it down. I searched for comparable public datasets but could not find one with the same hourly history. At the same time, paying $5 every month to keep this little collector alive did not make much sense 🙂. Instead of deleting the history, I moved the collector to GitHub Actions and made the dataset public.

I hope it will be useful for research, experiments, visualizations, or simply understanding how stories move through Hacker News.

## Dataset

The dataset lives in the `data` branch and is organized by UTC month:

```text
2026-08/
├── stories.csv
├── 2026-08-29.csv
├── 2026-08-30.csv
└── 2026-08-31.csv
```

Each `stories.csv` contains metadata for stories first observed in the Hacker News top 500 during that month:

- `id` — Hacker News item ID;
- `created_at` — original publication time on Hacker News;
- `added_at` — when the collector first observed the story in the top 500;
- `title` and `url`;
- `max_score` — highest score observed for the story;
- `max_comments` — highest comment count observed for the story.

Each daily CSV contains one row per story and UTC hour:

- `hour` — UTC hour from `0` to `23`;
- `id` — Hacker News item ID;
- `score` — maximum score observed during that hour;
- `comments` — maximum comment count observed during that hour;
- `best_rank` — best position observed during that hour, where `1` is the highest rank.

Full rank tracking was added in late August 2026. Most earlier rows therefore have no `best_rank`. I reconstructed ranks for some older stories from a separate archive of the top 30, so historical rank values appear occasionally but are limited to positions `1–30`.

## Possible uses

The dataset can be used to study:

- how long it takes a story to reach the top 500 after publication;
- how quickly its score and comment count grow or decline;
- how long successful stories remain visible;
- how different topics, titles, and domains perform;
- which kinds of Show HN or Ask HN posts receive the most attention;
- how posting time and day of the week correlate with performance;
- how stories climb through or fall out of the ranking.

These are observational data, so they can show patterns and correlations—not guarantee that posting at a particular time will make a submission successful.

## Usage

Clone the repository and build a SQLite database from the CSV dataset:

```bash
git clone https://github.com/vladkens/hn-timeseries.git
cd hn-timeseries
uv run hn.py export data/ynews.db
```

The resulting SQLite database contains `stories` and `stories_metrics` tables and is convenient for SQL queries and further analysis.

The collector is also a standalone uv script. To run your own persistent SQLite collector:

```bash
uv run hn.py serve data/ynews.db
```

It collects the current Hacker News top stories four times per hour and continuously updates the database.

## License

Distributed under the [MIT License](LICENSE).
