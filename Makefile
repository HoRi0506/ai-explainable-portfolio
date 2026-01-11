.PHONY: setup test smoke graph ui

setup:
	uv venv --python=python3
	. .venv/bin/activate && uv pip install -q pandas numpy streamlit yfinance httpx tenacity pydantic pytest

test:
	uv run pytest -q

smoke:
	uv run python -c "import pandas,streamlit,yfinance,httpx,tenacity,pydantic; print('imports-ok')"

graph:
	uv run python -m app.cli --sectors AI --budget 1000000 | head -c 200

ui:
	STREAMLIT_BROWSER_GATHER_USAGE_STATS=false uv run streamlit run ui/app.py

