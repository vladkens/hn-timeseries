tag = ynews

dev:
	cargo watch -q -x 'run'

update:
	cargo upgrade -i

docker-build:
	docker build --network=host -t $(tag) .
	docker images -q $(tag) | xargs docker inspect -f '{{.Size}}' | xargs numfmt --to=iec

docker-run: docker-build
	docker rm --force $(tag) || true
	docker run --network=host $(args) -p 8080:8080 -v ./data:/app/data --env-file .env --name $(tag) $(tag)

deploy:
	fly deploy --ha=false

backup:
	fly ssh sftp get /data/ynews.db ./data/ynews-$(shell date +%Y%m%d-%H%M).db
