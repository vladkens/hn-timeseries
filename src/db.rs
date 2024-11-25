use chrono::Timelike;
use serde::Serialize;
use tokio::sync::OnceCell;
use tokio_rusqlite::{params, Connection, Result, Row};

use crate::{apis::HNStory, settings};

type ListOf<T> = std::result::Result<Vec<T>, rusqlite::Error>;

static ONCE: OnceCell<Connection> = OnceCell::const_new();

async fn get_db<'a>() -> &'a Connection {
  let conn = ONCE.get_or_init(|| async {
    let path = settings::get().db_path.clone();
    let conn = Connection::open(&path).await.expect(&format!("Failed to open database: {}", path));

    let r = conn.call(|conn| {
      let qs = "CREATE TABLE IF NOT EXISTS stories (
        id INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        title TEXT NOT NULL,
        url TEXT,
        score INTEGER NOT NULL,
        comments INTEGER NOT NULL,
        added_at INTEGER DEFAULT (strftime('%s', 'now')),
        clicks INTEGER DEFAULT 0,
        tg_msg_id INTEGER
      )";
      conn.execute(qs, []).expect("Failed to create table: stories");

      let qs = "CREATE TABLE IF NOT EXISTS stories_metrics (
        id INTEGER NOT NULL,
        datetime INTEGER NOT NULL,
        score INTEGER NOT NULL,
        comments INTEGER NOT NULL,
        PRIMARY KEY (id, datetime)
      )";
      conn.execute(qs, []).expect("Failed to create table: stories_metrics");

      Ok(())
    });

    let _ = r.await;
    conn
  });

  conn.await
}

async fn call<F, R>(cb: F) -> Result<R>
where
  F: FnOnce(&mut rusqlite::Connection) -> Result<R> + 'static + Send,
  R: Send + 'static,
{
  let conn = get_db().await;
  conn.call(cb).await
}

// Structs

trait FromRow: Sized {
  fn from_row(row: &Row) -> rusqlite::Result<Self>;
}

#[derive(Debug, Serialize)]
pub struct Story {
  pub id: u64,
  pub created_at: u64,
  pub title: String,
  pub url: Option<String>,
  pub score: u32,
  pub comments: u32,
  pub added_at: u64, // was added_at, but now time when target_score was reached
  pub clicks: u32,
  pub tg_msg_id: Option<i64>,
}

impl FromRow for Story {
  fn from_row(row: &Row) -> rusqlite::Result<Self> {
    Ok(Self {
      id: row.get("id")?,
      created_at: row.get("created_at")?,
      title: row.get("title")?,
      url: row.get("url")?,
      score: row.get("score")?,
      comments: row.get("comments")?,
      added_at: row.get("added_at")?,
      clicks: row.get("clicks")?,
      tg_msg_id: row.get("tg_msg_id")?,
    })
  }
}

#[derive(Debug, Serialize)]
pub struct StoryMetrics {
  pub datetime: String,
  pub score: u32,
  pub comments: u32,
}

impl FromRow for StoryMetrics {
  fn from_row(row: &Row) -> rusqlite::Result<Self> {
    Ok(Self {
      datetime: row.get("datetime")?,
      score: row.get("score")?,
      comments: row.get("comments")?,
    })
  }
}

// Queries

pub async fn list_stories() -> Result<Vec<Story>> {
  call(|conn| {
    let qs = "SELECT * FROM stories WHERE added_at > 0 ORDER BY added_at DESC LIMIT 200";

    let mut stmt = conn.prepare(qs)?;
    let rows = stmt.query_map([], Story::from_row)?;
    let rows = rows.collect::<ListOf<_>>()?;

    Ok(rows)
  })
  .await
}

pub async fn list_unposted_stories(max_count: u32) -> Result<Vec<Story>> {
  call(move |conn| {
    let qs = format!(
      "SELECT * FROM stories WHERE tg_msg_id IS NULL AND score >= {} ORDER BY added_at ASC LIMIT {}",
      settings::get().target_score, max_count
    );

    let mut stmt = conn.prepare(&qs)?;
    let rows = stmt.query_map([], Story::from_row)?.collect::<ListOf<_>>()?;
    Ok(rows)
  })
  .await
}

pub async fn insert_story(story: &HNStory) -> Result<()> {
  let s = story.clone();
  call(move |conn| {
    // let qs = "INSERT INTO stories (id, created_at, title, url, score, comments, added_at)
    // VALUES (?, ?, ?, ?, ?, ?, 0) ON CONFLICT(id) DO NOTHING";

    let qs = "
    INSERT INTO stories AS t (id, created_at, title, url, score, comments, added_at)
    VALUES (?, ?, ?, ?, ?, ?, 0)
    ON CONFLICT(id) DO UPDATE SET
    score = MAX(t.score, excluded.score), comments = MAX(t.comments, excluded.comments)
    ";

    conn.execute(qs, params![s.id, s.time, s.title, s.url, s.score, s.descendants])?;
    Ok(())
  })
  .await
}

pub async fn mark_posted_tg(story_id: u64, tg_msg_id: i64) -> Result<()> {
  call(move |conn| {
    let qs = "UPDATE stories SET tg_msg_id=? WHERE id=?";

    conn.execute(qs, params![tg_msg_id, story_id])?;
    Ok(())
  })
  .await
}

pub async fn get_story_url(story_id: u64) -> Result<Option<String>> {
  call(move |conn| {
    let qs = "UPDATE stories SET clicks = clicks + 1 WHERE id = ? RETURNING url";

    let mut stmt = conn.prepare(qs)?;
    let mut rows = stmt.query(params![story_id])?;
    Ok(rows.next()?.map(|row| row.get(0).unwrap()))
  })
  .await
}

pub async fn mark_story_eligible(story_id: u64) -> Result<bool> {
  call(move |conn| {
    let qs = "UPDATE stories SET added_at = strftime('%s', 'now') WHERE id = ? AND added_at = 0";
    let mut stmt = conn.prepare(qs)?;
    stmt.execute(params![story_id])?;
    Ok(conn.changes() > 0)
  })
  .await
}

pub async fn update_story_metrics(story: &HNStory) -> Result<()> {
  let s = story.clone();
  call(move |conn| {
    let qs = "
    INSERT INTO stories_metrics AS t (id, datetime, score, comments)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(id, datetime) DO UPDATE SET
    score = MAX(t.score, excluded.score), comments = MAX(t.comments, excluded.comments)
    ";

    let dt = chrono::Utc::now();
    let dt = dt.with_minute(0).unwrap();
    let dt = dt.with_second(0).unwrap();
    let dt = dt.with_nanosecond(0).unwrap();
    let dt = dt.timestamp();

    conn.execute(qs, params![s.id, dt, s.score, s.descendants])?;
    Ok(())
  })
  .await
}

pub async fn get_story_metrics(story_id: u64) -> Result<Vec<StoryMetrics>> {
  call(move |conn| {
    let qs = "
    SELECT strftime('%Y-%m-%d %H:00', datetime(datetime, 'unixepoch')) AS datetime, score, comments
    FROM stories_metrics
    WHERE id = ?
    ORDER BY datetime ASC";

    let mut stmt = conn.prepare(qs)?;
    let rows = stmt.query_map(params![story_id], StoryMetrics::from_row)?;
    let rows = rows.collect::<ListOf<_>>()?;

    Ok(rows)
  })
  .await
}

pub async fn list_best_stories(days: u32, page: u32) -> Result<Vec<Story>> {
  let qs = "
  SELECT
    m.id, MAX(m.score) AS score, MAX(m.comments) AS comments,
    s.created_at, s.title, s.url, s.added_at, s.clicks, s.tg_msg_id 
  FROM stories_metrics m
  INNER JOIN stories s ON s.id = m.id
  WHERE datetime(datetime, 'unixepoch') >= datetime('now', '-1 day')
  GROUP BY m.id
  ORDER BY score DESC
  ";

  let per_page = 50;
  let offset = (page - 1) * per_page;
  let qs = format!("{} LIMIT {} OFFSET {}", qs, per_page, offset);
  let qs = qs.replace("-1 day", &format!("-{days} day"));

  call(move |conn| {
    let mut stmt = conn.prepare(&qs)?;
    let rows = stmt.query_map([], Story::from_row)?;
    let rows = rows.collect::<ListOf<_>>()?;

    Ok(rows)
  })
  .await
}
