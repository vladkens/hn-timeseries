use axum::extract::Path;
use axum::response::{IntoResponse, Redirect};
use axum::routing::get;
use axum::{Json, Router};
use server::{AppError, Res};

mod apis;
mod db;
mod jobs;
mod server;
mod settings;

async fn run_jobs() {
  match jobs::sync_hn().await {
    Err(err) => tracing::error!("Failed syncing HN: {}", err),
    _ => {}
  };

  match jobs::sync_tg().await {
    Err(err) => tracing::error!("Failed syncing TG: {}", err),
    _ => {}
  }
}

async fn index() -> Res<impl IntoResponse> {
  Ok(Json(serde_json::json!({ "status": "ok" })))
}

async fn navigate_story(Path(story_id): Path<u64>) -> Res<impl IntoResponse> {
  match db::get_story_url(story_id).await? {
    Some(story_url) => Ok(Redirect::permanent(&story_url)),
    None => Err(AppError::not_found()),
  }
}

async fn navigate_comments(Path(story_id): Path<u64>) -> Res<impl IntoResponse> {
  Ok(Redirect::permanent(&format!("https://news.ycombinator.com/item?id={}", story_id)))
}

async fn list_stories() -> Res<impl IntoResponse> {
  let stories = db::list_stories().await?;
  Ok(Json(stories))
}

async fn story_metrics(Path(story_id): Path<u64>) -> Res<impl IntoResponse> {
  let metrics = db::get_story_metrics(story_id).await?;
  Ok(Json(metrics))
}

async fn best_stories() -> Res<impl IntoResponse> {
  let stories = db::list_best_stories(7, 1).await?;
  Ok(Json(stories))
}

async fn unposted_stories() -> Res<impl IntoResponse> {
  let stories = db::list_unposted_stories(100).await?;
  Ok(Json(stories))
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
  settings::init();

  let mut scheduler = tokio_cron::Scheduler::utc();
  scheduler.add(tokio_cron::Job::new("0 */10 * * * *", run_jobs));

  let app = Router::new() //
    .route("/s/:story_id", get(navigate_story))
    .route("/c/:story_id", get(navigate_comments))
    .route("/api/latest", get(list_stories))
    .route("/api/best", get(best_stories))
    .route("/api/unposted", get(unposted_stories))
    .route("/api/metrics/:story_id", get(story_metrics))
    .route("/sync", get(run_jobs))
    .route("/", get(index));

  let host = std::env::var("HOST").unwrap_or("127.0.0.1".to_string());
  let port = std::env::var("PORT").unwrap_or("8080".to_string());
  let addr = format!("{}:{}", host, port);
  Ok(server::run_server(&addr, app).await?)
}
