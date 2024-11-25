use crate::{apis, db, settings};
use anyhow::Ok;
use std::sync::Arc;
use tokio::sync::Semaphore;

fn timeago(t1: u64, t2: u64) -> String {
  let delta = ((t2 - t1) / 60).max(1); // in minutes
  if delta < 60 {
    format!("{} minute{}", delta, if delta > 1 { "s" } else { "" })
  } else if delta < 1440 {
    format!("{} hour{}", delta / 60, if delta > 120 { "s" } else { "" })
  } else {
    format!("{} day{}", delta / 1440, if delta > 2880 { "s" } else { "" })
  }
}

async fn story_sync(story_id: u64) -> anyhow::Result<()> {
  let story = apis::hn_story(story_id).await?;
  db::insert_story(&story).await?;
  db::update_story_metrics(&story).await?;

  if story.score >= settings::get().target_score {
    if db::mark_story_eligible(story.id).await? {
      tracing::info!("Story eligible (id: {}, score: {})", story.id, story.score);
    }
  }

  Ok(())
}

async fn story_post_tg(story: &db::Story) -> anyhow::Result<i64> {
  let hn_url = format!("https://news.ycombinator.com/item?id={}", story.id);
  let short_url = format!("{}/s/{}", settings::get().public_url, story.id);
  let short_comments = format!("{}/c/{}", settings::get().public_url, story.id);

  let flag = if (story.added_at - story.created_at) / 3600 < 4 { "🔥 " } else { "" };
  let tago = timeago(story.created_at, story.added_at);
  let title = html_escape::encode_text(&story.title);

  let msg = format!("{flag}<b>{title}</b> ({}+ in {tago})\n\n", story.score);
  let msg = match story.url {
    Some(_) => format!("{msg}<b>Link:</b> {short_url}\n<b>Comments:</b> {short_comments}"),
    None => format!("{msg}<b>Link:</b> {}\n", hn_url),
  };

  let mut buttons: Vec<apis::TGButton> = Vec::new();
  if story.url.is_some() {
    buttons.push(apis::TGButton { text: "Read".to_string(), url: story.url.clone().unwrap() });
  }
  buttons.push(apis::TGButton { text: format!("Comments ({}+)", story.comments), url: hn_url });

  let msg_id = apis::tg_post(settings::get().tg_channel.as_ref(), &msg, buttons).await?;
  db::mark_posted_tg(story.id, msg_id).await?;
  Ok(msg_id)
}

pub async fn sync_hn() -> anyhow::Result<()> {
  let sem = Arc::new(Semaphore::new(20));
  for story_id in apis::hn_top_stories().await? {
    let permit = Arc::clone(&sem).acquire_owned().await?;
    tokio::task::spawn(async move {
      let _permit = permit;
      match story_sync(story_id.clone()).await {
        Err(err) => tracing::error!("Failed processing story {}: {}", story_id, err),
        _ => {}
      }
    });
  }

  Ok(())
}

pub async fn sync_tg() -> anyhow::Result<()> {
  let stories = db::list_unposted_stories(settings::get().tg_max_posts).await?;
  for story in stories {
    match story_post_tg(&story).await {
      Result::Ok(msg_id) => {
        tracing::info!("TG: Story posted, story_id={} msg_id={}", story.id, msg_id)
      }
      Err(err) => tracing::error!("TG: Failed to post, story_id={} err={}", story.id, err),
    }
  }

  Ok(())
}
