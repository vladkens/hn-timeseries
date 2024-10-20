import asyncio
import os
import time
from typing import Awaitable, Callable


def env_str(name: str, default_value: str) -> str:
    return os.environ.get(name, default_value)


def env_num(name: str, default_value: int) -> int:
    return int(os.environ.get(name, default_value))


def call_after(cb: Callable[[], Awaitable]):
    def decorator(fn: Callable[[], Awaitable]):
        async def wrapper():
            try:
                return await fn()
            finally:
                await cb()

        return wrapper

    return decorator


async def map_limited(func, iterable, limit):
    sem = asyncio.Semaphore(limit)

    async def sem_task(i):
        async with sem:
            return await func(i)

    return await asyncio.gather(*[sem_task(i) for i in iterable])


def timeago(story_time: int, base_time: int | None = None) -> str:
    base_time = base_time or int(time.time())

    delta = max(1, int((base_time - story_time) / 60))  # in minutes
    if delta < 60:
        return f"{delta} minutes" if delta > 1 else "1 minute"
    elif delta < 1440:
        return f"{delta // 60} hours" if delta > 120 else "1 hour"
    else:
        return f"{delta // 1440} days" if delta > 2880 else "1 day"
