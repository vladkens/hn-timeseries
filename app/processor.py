import html

from loguru import logger

from . import apis, db, settings
from .utils import map_limited, timeago


async def process_story(story_id: int) -> None:
    if await db.check_story_added(story_id):
        return

    story = await apis.call_hn(f"item/{story_id}")
    score, comments = story.get("score", 0), story.get("descendants", 0)
    if score < settings.TARGET_SCORE:
        return

    await db.add_story(
        story_id=story_id,
        created_at=story["time"],
        title=story["title"],
        url=story.get("url"),
        score=score,
        comments=comments,
    )

    logger.info(f"{story_id=} added ({score=}, {comments=})")


async def post_story_tg(story: db.Story) -> None:
    hn_url = f"https://news.ycombinator.com/item?id={story.id}"
    short_url = f"{settings.PUBLIC_URL}/s/{story.id}"
    short_comments = f"{settings.PUBLIC_URL}/c/{story.id}"

    flag = "🔥 " if (story.added_at - story.created_at) // 3600 < 4 else ""
    tago = timeago(story.created_at, story.added_at)
    title = html.escape(story.title)
    msg = f"{flag}<b>{title}</b> ({story.score}+ in {tago})\n\n"

    if story.url:
        msg += f"<b>Link:</b> {short_url}\n"
        msg += f"<b>Comments:</b> {short_comments}"
    else:
        msg += f"<b>Link:</b> {hn_url}\n"

    buttons: list[apis.TgButton] = []
    buttons.append({"text": "Read", "url": story.url}) if story.url else None
    buttons.append({"text": f"Comments ({story.comments}+)", "url": hn_url})

    msg_id = await apis.post_tg(settings.TG_CHANNEL, msg, buttons)
    logger.info(f"Posted {story.id=} to Telegram {msg_id=}")
    await db.mark_posted_tg(story.id, msg_id)


async def sync_hn():
    async def process_safe(story_id: int):
        try:
            await process_story(story_id)
        except Exception as e:
            logger.error(f"Error processing story {story_id}: {e}")

    stories: list[int] = await apis.call_hn("topstories")
    await map_limited(process_safe, stories, 10)
    logger.info(f"Synced {len(stories)} stories")


async def sync_tg():
    stories = await db.get_unposted_stories()
    for story in stories:
        await post_story_tg(story)
