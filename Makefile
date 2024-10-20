tag = ynews

dev:
	uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload

deploy:
	fly deploy --ha=false

copy-db:
	fly ssh sftp get /data/ynews.db ./data/ynews-$(shell date +%Y%m%d-%H%M).db

docker-build:
	docker build -t $(tag) .
	docker images -q $(tag) | xargs docker inspect -f '{{.Size}}' | xargs numfmt --to=iec

docker-run: docker-build
	docker rm --force $(tag) || true
	docker run -p 8080:8080 -v ./data:/app/data --env-file .env --name $(tag) $(tag)
