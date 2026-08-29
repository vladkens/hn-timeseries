.PHONY: prepare update check docker-build docker-run deploy backup

tag = hn-timeseries

prepare:
	uv sync
	uv run ruff check --select I --fix .
	uv run ruff format .
	$(MAKE) check

check:
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
	docker run $(args) -v ./data:/data --name $(tag) $(tag)

deploy:
	fly deploy --ha=false

backup:
	fly ssh sftp get /data/ynews.db ./data/ynews-$(shell date +%Y%m%d-%H%M).db
