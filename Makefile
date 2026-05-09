.PHONY: help sync test online-test lint format type-check video

help:
	@printf "Available targets:\n"
	@printf "  sync         Sync dependencies with uv\n"
	@printf "  test         Run the test suite\n"
	@printf "  online-test  Run daily-message online tests\n"
	@printf "  lint         Run type checks, ruff, and black checks\n"
	@printf "  format       Format and autofix source and tests\n"
	@printf "  type-check   Run mypy\n"
	@printf "  video        Build the city-poster video\n"

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
