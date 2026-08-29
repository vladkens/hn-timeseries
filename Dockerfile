FROM python:3.14-alpine
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY collector.py .
COPY crontab /etc/crontabs/root

CMD ["crond", "-f", "-l", "8", "-L", "/dev/stdout"]
