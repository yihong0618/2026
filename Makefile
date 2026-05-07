.PHONY: sync test online-test lint format type-check video

sync:
	uv sync --all-groups

test:
	uv run pytest -q

online-test:
	uv run python tests/test_daily_message.py

lint: type-check
	uv run ruff check src tests
	uv run black --check src tests

format:
	uv run ruff check --fix src tests
	uv run black src tests

type-check:
	uv run mypy src tests

video:
	uv run make-city-video
