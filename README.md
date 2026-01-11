# AI Explainable Portfolio

Explain-first multi-agent app that researches sectors, screens tickers, fetches fundamentals, scores valuation, analyzes risk, allocates weights, and plans mock trades. Streamlit UI with reproducible, typed pipeline.

빠른 시작 (Quick start)

- Python 환경 생성: `uv venv && . .venv/bin/activate`
- 필수 패키지 설치: `uv pip install -q pandas numpy streamlit yfinance httpx tenacity pydantic pytest`
- 환경 변수: `.env.example`를 `.env`로 복사 후 키 입력(없어도 무료 경로로 동작)
- UI 실행: `uv run streamlit run ui/app.py`
- 테스트(무료): `uv run pytest -q` (외부 유료 API 미사용)

자세한 진행 계획은 `TASKS.md`, 에이전트 사양은 `AGENTS.md`를 참고하세요.

주의/면책

- 본 앱은 투자 조언을 제공하지 않으며, 데이터의 정확성을 보장하지 않습니다.
- 수치/날짜는 툴(yfinance/SEC 등)에서 온 값만 사용합니다.
- 외부 API 호출은 레이트리밋의 영향을 받을 수 있으며, 실패 시 자동 재시도/폴백이 적용됩니다.
