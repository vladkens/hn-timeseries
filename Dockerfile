FROM python:3.14-alpine
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY collector.py server.py ./

CMD ["uv", "run", "--frozen", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
