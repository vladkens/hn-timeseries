.PHONY: prepare update check

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
