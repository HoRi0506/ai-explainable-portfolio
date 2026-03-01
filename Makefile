.PHONY: setup test smoke lint

setup:
	uv venv --python=python3.11
	uv pip install -e ".[dev]"

test:
	uv run pytest -q

smoke:
	uv run python -c "from tools import yfinance_client; print('tools-ok')"
	uv run python -c "import schemas, engine, adapters, agents; print('packages-ok')"

lint:
	uv run mypy schemas/ engine/ adapters/ agents/ tools/
