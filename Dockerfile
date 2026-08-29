FROM python:3.14-alpine
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
COPY hn.py ./
RUN uv sync --script hn.py

CMD ["uv", "run", "--offline", "hn.py", "serve", "/data/ynews.db"]
