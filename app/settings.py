import logging
import os

from dotenv import load_dotenv

from .utils import env_num, env_str

load_dotenv()


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return msg.find("GET /health") == -1


# https://github.com/encode/starlette/issues/864#issuecomment-653076434
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

DB_PATH = env_str("DB_PATH", "data/hn.db")
PUBLIC_URL = env_str("PUBLIC_URL", "http://localhost:8080")
TARGET_SCORE = env_num("TARGET_SCORE", 150)
TG_CHANNEL = env_str("TG_CHANNEL", "@hnews_top")
TG_TOKEN = os.environ["TG_TOKEN"]
