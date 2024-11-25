use std::env;
use std::sync::OnceLock;

use tracing::level_filters::LevelFilter;
use tracing_subscriber::EnvFilter;

fn env_str(name: &str, default: Option<&str>) -> String {
  match env::var(name) {
    Ok(val) => val,
    Err(_) => match default {
      Some(val) => val.to_string(),
      None => {
        tracing::error!("{} must be set", name);
        std::process::exit(1);
      }
    },
  }
}

fn env_num<T: std::str::FromStr>(name: &str, default: Option<&str>) -> T {
  match env_str(name, default).parse() {
    Ok(num) => num,
    Err(_) => {
      tracing::error!("{} must be a number", name);
      std::process::exit(1);
    }
  }
}

static ONCE: OnceLock<Settings> = OnceLock::new();

pub struct Settings {
  pub db_path: String,
  pub public_url: String,
  pub target_score: u32,
  pub tg_channel: String,
  pub tg_token: String,
  pub tg_max_posts: u32,
}

pub fn load() {
  if ONCE.get().is_some() {
    return;
  }
}

pub fn init() {
  ONCE.get_or_init(|| {
    dotenvy::dotenv().ok();

    let f = EnvFilter::builder().with_default_directive(LevelFilter::INFO.into()).from_env_lossy();
    tracing_subscriber::fmt().with_env_filter(f).with_target(false).compact().init();

    Settings {
      db_path: env_str("DB_PATH", Some("data/hn.db")),
      public_url: env_str("PUBLIC_URL", Some("https://example.com")),
      target_score: env_num("TARGET_SCORE", Some("150")),
      tg_channel: env_str("TG_CHANNEL", Some("@hnews_top")),
      tg_token: env_str("TG_TOKEN", None),
      tg_max_posts: env_num::<u32>("TG_MAX_POSTS", Some("1")).max(1),
    }
  });
}

pub fn get() -> &'static Settings {
  ONCE.get().expect("Settings not initialized")
}
