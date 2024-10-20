FROM python:3.13-alpine
WORKDIR /app

COPY ./requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . /app

ENV HOST=0.0.0.0 PORT=8080
EXPOSE ${PORT}
HEALTHCHECK CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:${PORT}/health || exit 1
CMD uvicorn app.main:app --host ${HOST} --port ${PORT}
