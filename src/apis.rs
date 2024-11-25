use crate::settings;

#[derive(Debug, Clone, serde::Deserialize)]
pub struct HNStory {
  pub id: u64,
  pub by: String,
  pub title: String,
  pub score: u32,
  pub time: u64,
  pub url: Option<String>,
  pub descendants: u64,
  // pub kids: Option<Vec<u64>>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct TGButton {
  pub text: String,
  pub url: String,
}

fn get_client() -> reqwest::Client {
  let client = reqwest::Client::builder()
    .timeout(std::time::Duration::from_secs(10))
    .build()
    .expect("failed to create http client");

  client
}

async fn call_hn<T: serde::de::DeserializeOwned>(method: &str) -> anyhow::Result<T> {
  let url = format!("https://hacker-news.firebaseio.com/v0/{}.json", method);
  let rep = get_client().get(url).send().await?.error_for_status()?;
  let dat = rep.json::<T>().await?;
  Ok(dat)
}

pub async fn hn_top_stories() -> anyhow::Result<Vec<u64>> {
  let dat = call_hn::<Vec<u64>>("topstories").await?;
  Ok(dat)
}

pub async fn hn_story(story_id: u64) -> anyhow::Result<HNStory> {
  let dat = call_hn::<HNStory>(&format!("item/{}", story_id)).await?;
  Ok(dat)
}

pub async fn tg_post(chat_id: &str, text: &str, buttons: Vec<TGButton>) -> anyhow::Result<i64> {
  // https://core.telegram.org/bots/api#sendmessage
  let mut msg = serde_json::json!({
    "chat_id": chat_id,
    "text": text,
    "parse_mode": "HTML",
    "disable_notification": true,
  });

  if buttons.len() > 0 {
    // inline_keyboard – array of arrays, each array represents a row of buttons
    // this fn expects a single row
    let buttons = serde_json::json!({"inline_keyboard": [buttons]});
    msg["reply_markup"] = buttons;
  }

  let url = format!("https://api.telegram.org/bot{}/sendMessage", settings::get().tg_token);
  let rep = get_client().post(url).json(&msg).send().await?;
  if !rep.status().is_success() {
    let (status, err) = (rep.status(), rep.text().await?);
    anyhow::bail!("Failed to post to {}: {} - {}", chat_id, status, err);
  }

  let dat = rep.json::<serde_json::Value>().await?;
  if !dat["ok"].as_bool().unwrap() {
    anyhow::bail!("Failed to post to {}: {}", chat_id, dat);
  }

  Ok(dat["result"]["message_id"].as_i64().unwrap())
}
