use anyhow::Result;
use axum::{
  http::StatusCode,
  response::{IntoResponse, Json, Response},
  routing::get,
  Router,
};
use tower_http::trace::{self, TraceLayer};
use tracing::Level;

pub struct AppError(anyhow::Error);
pub type Res<T> = Result<T, AppError>;

impl AppError {
  pub fn new<T>(msg: &str) -> Res<T> {
    Err(Self(anyhow::anyhow!(msg.to_string())))
  }
}

impl AppError {
  pub fn not_found() -> Self {
    Self(anyhow::anyhow!(axum::http::StatusCode::NOT_FOUND))
  }
}

impl IntoResponse for AppError {
  fn into_response(self) -> Response {
    let msg = serde_json::json!({ "code": 400, "message": self.0.to_string() });
    (StatusCode::BAD_REQUEST, Json(msg)).into_response()
  }
}

impl<E: Into<anyhow::Error>> From<E> for AppError {
  fn from(err: E) -> Self {
    Self(err.into())
  }
}

async fn health() -> impl IntoResponse {
  let msg = serde_json::json!({ "status": "ok" });
  (StatusCode::OK, axum::response::Json(msg))
}

async fn not_found() -> impl IntoResponse {
  let msg = serde_json::json!({ "code": 404, "message": "not found" });
  (StatusCode::NOT_FOUND, Json(msg))
}

// https://github.com/tokio-rs/axum/discussions/1894
async fn shutdown_signal() {
  use tokio::signal;

  let ctrl_c = async {
    signal::ctrl_c().await.expect("failed to install Ctrl+C handler");
  };

  let terminate = async {
    signal::unix::signal(signal::unix::SignalKind::terminate())
      .expect("failed to install signal handler")
      .recv()
      .await;
  };

  tokio::select! {
    _ = ctrl_c => {},
    _ = terminate => {},
  }
}

pub async fn run_server(addr: &str, app: Router) -> anyhow::Result<()> {
  let app = app
    .layer(
      TraceLayer::new_for_http()
        .make_span_with(trace::DefaultMakeSpan::new().level(Level::INFO))
        .on_response(trace::DefaultOnResponse::new().level(Level::INFO)),
    )
    .route("/health", get(health))
    .fallback_service(get(not_found));

  let listener = tokio::net::TcpListener::bind(&addr).await?;
  tracing::info!("listening on http://{}", addr);
  axum::serve(listener, app).with_graceful_shutdown(shutdown_signal()).await?;

  Ok(())
}
