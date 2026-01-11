# AGENTS

본 문서는 8개 에이전트의 역할, 입출력 스키마 초안, 규칙을 요약합니다. 숫자·날짜·계산값은 툴 결과만 사용합니다.

## Sector Researcher
- Input: `sectors: list[str]`, `keywords: list[str]`, `k: int`
- Tools: `firecrawl.search`, `firecrawl.scrape`
- Output: `candidates: list[{ ticker: str, name: str|None, source_urls: list[str] }]`
- Rules: 모든 주장에 출처 포함, 중복 제거, 최신성 우선

## Ticker Screener
- Input: `candidates: list[str|{ticker:str}]`
- Tools: `yfinance.Ticker.fast_info`, `history`
- Output: `tradable: list[{ ticker, exchange, avg_dollar_vol, reason_if_excluded? }]`
- Rules: 유동성/상장시장/거래정지 필터, 제외 사유 기록

## Fundamentals Fetcher
- Input: `tickers: list[str]`
- Tools: yfinance(Info/FastInfo), SEC 10-K/10-Q 요약(가능 범위)
- Output: `facts_by_ticker: dict[str, { revenue, ebit, fcf, debt, asof_date, sources[] }]`
- Rules: 결측 허용(명시), 날짜/단위 표기, 링크 유지

## Valuation Analyst
- Input: `facts_by_ticker`
- Output: `scores: list[{ ticker, score: float(0~1), rank: int, rationale_md: str }]`
- Rules: 수치=툴, P/E·EV/EBITDA·FCF Yield 가중 합산(가중치 명시), None-safe

## Insider & Ownership Analyst
- Input: `tickers: list[str]`
- Tools: SEC Form 4 메타/요약(가능 범위)
- Output: `insights: list[{ ticker, bullets: list[str], sources: list[str] }]`
- Rules: 최근 이벤트 우선, 날짜/링크 포함

## Risk Analyst
- Input: `tickers: list[str]`
- Tools: `yfinance.history`, `pandas`
- Output: `corr: pd.DataFrame`, `risk_notes: str`
- Rules: 윈도우/빈도 명시, 이상치/결측 처리

## Allocator
- Input: `tickers: list[str]`, `constraints: {min_w, max_w, sector_caps, method}`
- Output: `weights: dict[str, float] (sum=1.0)`
- Rules: 제약 100% 준수, 기본 균등/옵션 리스크-패리티

## Trade Planner
- Input: `weights`, `budget`, `fee_bps`, `slippage_bps`, `t_settle='T+1'`
- Output: `orders: list[{ ticker, qty:int, est_price, fee, total }]`
- Rules: 반올림/체결가 가정 명시, 합계≈예산
