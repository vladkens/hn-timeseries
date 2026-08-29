.PHONY: prepare update

tag = ynews

prepare:
	uv sync
	uv run ruff check --select I --fix .
	uv run ruff format .
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check

update:
	uv sync --upgrade --all-groups
	uv --preview-features audit-command audit

docker-build:
	docker build --network=host -t $(tag) .
	docker images -q $(tag) | xargs docker inspect -f '{{.Size}}' | xargs numfmt --to=iec

docker-run: docker-build
	docker rm --force $(tag) || true
	docker run --network=host $(args) -v ./data:/data -e DB_PATH=/data/ynews.db --name $(tag) $(tag)

deploy:
	fly deploy --ha=false

backup:
	fly ssh sftp get /data/ynews.db ./data/ynews-$(shell date +%Y%m%d-%H%M).db
