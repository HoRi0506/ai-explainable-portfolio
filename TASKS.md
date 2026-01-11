# AI 설명형 주식 에이전트 앱 – Codex 실행형 작업보드 (TASKS.md)

> 목적: **설명 중심**의 주식 선택·배분·주문 계획을 생성하는 멀티에이전트 앱을 Codex CLI/IDE로 **계획→실행→피드백→완료**까지 자동화/반복 가능하도록 정의한 작업 보드입니다. 이 문서를 리포지토리 루트에 `TASKS.md`로 두고, Codex가 순차적으로 처리하도록 설계되었습니다.

---

## 0) 프로젝트 개요

* **핵심 목표**: 사용자가 섹터(원자력/우주/AI/블록체인), 예산, 종목수, 비중 제약을 입력하면, 8개 에이전트가 **병렬**로 리서치·분석을 수행하고 **이유/출처**를 포함한 포트폴리오 설명과 **모의 주문 계획**을 생성.
* **설명 우선 원칙**

  * LLM이 말하는 **숫자/날짜는 툴 출력만** 사용(yfinance/SEC 등).
  * 모든 주장 옆에 **출처 링크**가 있도록 강제.
  * UI에서 종목 카드로 **“왜 담았나”**를 항목별로 확인 가능.
* **에이전트(병렬)**

  1. 기업 찾는 **섹터 리서처** (Firecrawl 검색/스크랩)
  2. **티커 스크리너** (상장/유동성/거래 가능 여부)
  3. **펀더멘털 페처** (매출/영업익/FCF 등)
  4. **밸류에이션 애널리스트** (멀티팩터 점수화)
  5. **인사이더·오너십 애널리스트** (Form 4 등 가능 범위)
  6. **리스크 애널리스트** (상관행렬/섹터 편중)
  7. **알로케이터** (비중 산출; 균등/리스크-패리티 옵션)
  8. **트레이드 플래너** (T+1·수수료 가정 모의 주문서)
* **오케스트레이션**: CrewAI(병렬 워크플로 간단) 또는 LangGraph(맵/리듀스·분기 정교).
* **UI**: Streamlit
* **툴/데이터**: Firecrawl(검색/스크랩), yfinance(가격/일부 재무), SEC EDGAR(10-K/10-Q/Form 4), Pandas/NumPy

---

## 1) 요구사항 (Requirements)

### 1.1 기능 요구사항 (FR)

* [ ] 사용자는 섹터·총예산·종목수·최대/최소 비중·섹터 한도를 입력할 수 있다.
* [ ] 리서처는 웹 검색/스크랩으로 후보 기업/티커를 수집하고 **출처 URL**을 유지한다.
* [ ] 스크리너는 거래 가능/유동성/상장시장 필터를 적용해 불량 티커를 제거한다.
* [ ] 펀더멘털 페처는 재무지표를 수집(가능한 필드: 매출/영업익/FCF/부채 등)한다.
* [ ] 밸류에이션 애널리스트는 P/E, EV/EBITDA, FCF Yield 등 **멀티팩터 점수**를 계산하고 근거를 기록한다.
* [ ] 인사이더·오너십 애널리스트는 내부자 매매(Form 4)·주요 주주 변화를 요약(가능 범위)한다.
* [ ] 리스크 애널리스트는 가격 히스토리를 기반으로 **상관행렬/히트맵**과 섹터 편중 경고를 제공한다.
* [ ] 알로케이터는 제약(최대/최소·섹터 한도)을 만족하는 비중을 산출한다(기본: 균등; 옵션: 리스크-패리티).
* [ ] 트레이드 플래너는 T+1 체결·수수료/슬리피지 가정으로 **종목별 수량/금액**을 산출한다(모의).
* [ ] 최종 리포트는 종목별 **선정 사유(수치/출처)**, 포트폴리오 요약, 리스크/상관 요약, 주문 계획을 제공한다.

### 1.2 비기능 요구사항 (NFR)

* [ ] 재현성: 파이프라인 입력/출력을 Pydantic 모델로 강타입화, 실행 로그 보존.
* [ ] 안정성: 외부 API 레이트리밋·오류에 대한 재시도/백오프/캐시.
* [ ] 비용/성능: 병렬 동시성 상한, LLM 토큰/스텝 상한, 중복 요청 캐시.
* [ ] 보안: `.env`로 키 분리, 비밀 유출 금지.
* [ ] 품질: 단위/통합 테스트(핵심 툴 래퍼·점수화 함수), 수동 UX 테스트 체크리스트.

---

## 2) 기술 스택 & 구조

* **LLM**: gpt-oss 20B(로컬/저비용), GPT-5/GPT-4o(정확도·툴호출 안정성↑)
* **오케스트레이션**: CrewAI 또는 LangGraph
* **UI**: Streamlit
* **데이터 툴**: Firecrawl, yfinance, SEC EDGAR(HTTP), Pandas/NumPy
* **형상관리/자동화**: Git, pre-commit, Makefile(옵션)

### 2.1 리포지토리 구조(권장)

```
ai-explainable-portfolio/
├─ app/
│  ├─ graph.py               # Orchestrator (CrewAI/LangGraph)
│  ├─ settings.py            # 기본 설정(섹터 프리셋/제약/수수료 등)
├─ agents/
│  ├─ sector_researcher.py
│  ├─ ticker_screener.py
│  ├─ fundamentals_fetcher.py
│  ├─ valuation_analyst.py
│  ├─ insider_ownership.py
│  ├─ risk_analyst.py
│  ├─ allocator.py
│  └─ trade_planner.py
├─ tools/
│  ├─ firecrawl_client.py    # search/scrape/crawl 래퍼(+캐시/리트라이)
│  ├─ yfinance_client.py     # 가격/정보
│  ├─ sec_client.py          # 10-K/10-Q/Form 4 파서(가능 범위)
│  └─ utils.py               # 로깅/캐시/백오프/타입
├─ ui/
│  └─ app.py                 # Streamlit 앱
├─ reports/
│  └─ templates/summary.md   # Markdown→PDF 템플릿(선정 사유/출처)
├─ tests/
│  ├─ test_firecrawl.py
│  ├─ test_yfinance.py
│  └─ test_valuation.py
├─ .env.example
├─ AGENTS.md                 # 에이전트 역할·규칙
└─ TASKS.md                  # (이 파일) Codex 작업 보드
```

---

## 3) 작업 진행 규칙 (Codex 운영 가이드)

* **상태 태그**: `[TODO]` 미시작 / `[DOING]` 진행중 / `[BLOCKED]` 대기 / `[DONE]` 완료
* **체크리스트**: 각 태스크는 명확한 **수행 기준**과 **검증 절차**를 가진다.
* **커밋 메시지**: 영어형 요약(`feat:`, `fix:`, `chore:` 등), 작은 단위로 빈번히.
* **완료(DoD)**: 수용기준 충족 + 테스트 통과 + 문서/샘플 반영 + 앱에서 확인.
* **피드백 루프**: UI/리포트 확인 후 수정 태스크를 즉시 Backlog에 생성.

---

## 4) 마일스톤 & 태스크 (순서대로 실행)

> **Codex 사용 팁**: 각 태스크는 **명령 예시**와 **검증 방법**을 포함합니다. 환경/명령은 팀 표준에 맞게 조정하세요.

### Milestone A — 초기화 & 기반 세팅

* **A-1 [DONE] 리포지토리 부트스트랩**

  * 수행: 디렉토리 스캐폴딩 생성, `README.md`, `.gitignore`, `.env.example` 배치
  * 수용기준: 구조가 위 스펙과 일치, `pre-commit`(옵션) 동작
  * 검증: `tree` 출력 확인, 린트/포맷 검사 통과
  * 검증(무료 테스트): `make smoke` 실행 → 임포트 OK 확인

* **A-2 [DONE] 환경 구성**

  * 수행: `venv/poetry` 중 택1, `pandas numpy streamlit yfinance httpx tenacity pydantic` 설치
  * 수용기준: `python -c "import pandas,streamlit"` 성공, `.env.example` 키 항목 기입
  * 검증(무료 테스트): `make setup && make test` 실행 → 테스트 통과

* **A-3 [DONE] AGENTS.md 초안**

  * 수행: 8개 에이전트 역할/입출력/규칙(숫자는 툴만) 문서화
  * 수용기준: 각 에이전트에 입력/출력 스키마 초안 포함
  * 검증(무료 테스트): `pytest -q`에서 문서/임포트 관련 에러 없음

### Milestone B — 데이터 툴 래퍼

* **B-1 [DONE] Firecrawl 래퍼 구현 (`tools/firecrawl_client.py`)**

  * 수행: search/scrape/crawl 함수 + 캐시·재시도(tenacity)
  * 수용기준: 키워드로 결과 리스트+URL 반환, 429/5xx 백오프
  * 검증(무료 테스트): 키 미설정 시 빈 결과 확인 `pytest -q tests/test_firecrawl.py`

* **B-2 [DONE] yfinance 래퍼 (`tools/yfinance_client.py`)**

  * 수행: `info(ticker)`, `history(ticker)` 구현(오류 처리/빈 DF 방지)
  * 수용기준: 최근 2년 종가·거래대금 로드, FastInfo/Info 일부 필드 제공
  * 검증(무료 테스트): `pytest -q tests/test_yfinance.py`

* **B-3 [DONE] SEC 래퍼 (`tools/sec_client.py`)**

  * 수행: 10-K/10-Q/Form 4 메타/요점 파서(간단 버전, 링크·날짜 유지)
  * 수용기준: 회사 CIK/최근 신고 링크 목록 반환, 기본 요약 필드
  * 검증(무료 테스트): `pytest -q tests/test_valuation.py`

### Milestone C — 에이전트 스켈레톤 & 병렬화

* **C-1 [DONE] 오케스트레이터 선택 & 초기 그래프 (`app/graph.py`)**

  * 수행: CrewAI 또는 LangGraph 중 선택, 입력→리서치→스크리닝→(티커 fan-out: 펀더멘털/밸류/인사이더/리스크 준비)→알로케이션→트레이드 플랜 흐름 구성
  * 수용기준: 더미 티커 2~3개로 end-to-end 데이터 플로 완료
  * 검증(무료 테스트): `make graph`로 JSON 출력, `pytest -q tests/test_graph.py`

* **C-2 [DONE] 섹터 리서처 (`agents/sector_researcher.py`)**

  * 수행: 섹터 프리셋 키워드로 Firecrawl 검색→티커 후보·출처 리스트 추출
  * 수용기준: 최소 5개 후보+출처 URL 포함 반환
  * 검증(무료 테스트): `pytest -q tests/test_sector_researcher.py`

* **C-3 [DONE] 티커 스크리너 (`agents/ticker_screener.py`)**

  * 수행: 거래정지/유동성/상장시장 필터(yfinance)
  * 수용기준: 불량 티커 제거, 남은 후보 ≥3개
  * 검증(무료 테스트): `pytest -q tests/test_ticker_screener.py`

* **C-4 [DONE] 펀더멘털 페처 (`agents/fundamentals_fetcher.py`)**

  * 수행: 매출/영업익/FCF/부채 등 수집(가용 범위), 실패 시 graceful degrade
  * 수용기준: 티커별 facts 딕셔너리 + 출처 링크/날짜 보존
  * 검증(무료 테스트): `pytest -q tests/test_fundamentals_fetcher.py`

* **C-5 [DONE] 밸류에이션 애널리스트 (`agents/valuation_analyst.py`)**

  * 수행: P/E, EV/EBITDA, FCF Yield 가중합으로 **점수** 산출, rationale MD 작성(수치=툴)
  * 수용기준: (0~1) 점수/랭크 반환, 근거 텍스트 포함
  * 검증(무료 테스트): `pytest -q tests/test_valuation.py`

* **C-6 [DONE] 인사이더·오너십 애널리스트 (`agents/insider_ownership.py`)**

  * 수행: Form 4/주요 주주 변동 요약(가능 범위), 링크·날짜 포함
  * 수용기준: 티커별 bullet 3개 이상(없을 시 "최근 Form 4 없음" 폴백)
  * 검증(무료 테스트): `pytest -q tests/test_insider_ownership.py`

* **C-7 [DONE] 리스크 애널리스트 (`agents/risk_analyst.py`)**

  * 수행: 가격 히스토리 적재→상관행렬 계산/요약
  * 수용기준: 상관 히트맵 데이터프레임/요약 텍스트
  * 검증(무료 테스트): `pytest -q tests/test_risk_analyst.py`

* **C-8 [DONE] 알로케이터 (`agents/allocator.py`)**

  * 수행: 균등배분(기본), 제약(최대/최소/섹터 한도) 충족
  * 수용기준: 가중치 합 1.0, 제약 위반 0건
  * 검증(무료 테스트): `pytest -q tests/test_allocator.py`

* **C-9 [DONE] 트레이드 플래너 (`agents/trade_planner.py`)**

  * 수행: T+1·수수료/슬리피지 가정으로 수량/금액 계산, 반올림 로직 포함
  * 수용기준: 종목별 수량/금액 표
  * 검증(무료 테스트): `pytest -q tests/test_trade_planner.py`

### Milestone D — Streamlit UI & 리포트

* **D-1 [DONE] UI 스켈레톤 (`ui/app.py`)**

  * 수행: 사이드바 입력(섹터/예산/종목수/제약), 실행 버튼, 결과 탭(요약/종목 설명/리스크/주문)
  * 수용기준: 로컬 실행, 더미 데이터 표시
  * 검증(무료 테스트): `make ui` 로컬 확인, 또는 `pytest -q tests/test_ui_import.py` 스모크

* **D-2 [DONE] UI↔그래프 연동**

  * 수행: 버튼 클릭→그래프 실행→상태/스피너→결과 렌더링(카드/표/다운로드)
  * 수용기준: 실제 데이터로 결과 표시, 오류 토스트
  * 검증(무료 테스트): `pytest -q tests/test_ui_import.py` + 수동 `make ui`

* **D-3 [DONE] 설명 리포트 템플릿 (`reports/templates/summary.md`)**

  * 수행: 종목별 선정 사유(수치/출처), 포트폴리오 요약, 상관 요약, 주문 계획
  * 수용기준: Markdown 렌더링 정상, 링크 클릭 가능
  * 검증(무료 테스트): `pytest -q tests/test_report_template.py`

### Milestone E — 품질·운영

* **E-1 [DONE] 캐시/리트라이/레이트리밋 공통화 (`tools/utils.py`)**

  * 수행: httpx+tenacity 백오프, 간단 캐시(.cache), 공통 `request_json`
  * 수용기준: 공개 API 호출 실패 시 재시도/None 반환, 캐시 I/O 정상
  * 검증(무료 테스트): `pytest -q tests/test_utils.py`

* **E-2 [DONE] 로깅/감사 흔적**

  * 수행: 표준 로거 + 파일 핸들러(`.logs/app.log`)
  * 수용기준: 실행 후 로그 파일 생성
  * 검증(무료 테스트): `pytest -q tests/test_utils.py::test_logger_writes_file`

* **E-3 [DONE] 단위/통합 테스트 보강 (`tests/…`)**

  * 수행: 핵심 함수에 대한 추가 테스트(밸류 normalize, 플래너 0예산, yfinance 오류 등)
  * 수용기준: 로컬 `pytest` 성공(무료 경로)
  * 검증(무료 테스트): `pytest -q` 전체 통과

* **E-4 [DONE] 문서화 & 예제 시나리오**

  * 수행: README에 실행방법/제약/면책 공지, 샘플 실행 커맨드
  * 수용기준: 신규 합류자 30분 내 로컬 실행 가능
  * 검증(무료 테스트): README 절차로 로컬 실행 확인

---

## 5) 검증 체크리스트 (End-to-End)

* [ ] 섹터=AI, 예산=10,000,000 KRW, 종목수=5, 최대비중=35% → 결과 표 생성
* [ ] 각 종목 카드에 **수치(툴 결과)**와 **출처 URL**가 모두 표시
* [ ] 상관행렬 요약과 섹터 편중 경고가 표시
* [ ] 주문 계획 합계가 예산에 근접(수수료/반올림 고려)
* [ ] 오류 발생 시 사용자에게 의미 있는 메시지와 재시도 안내

---

## 6) .env 스키마 (예시)

```
# .env.example
OPENAI_API_KEY=
OSS_BASE_URL=
OSS_API_KEY=
FIRECRAWL_API_KEY=
SEC_API_KEY=
```

---

## 7) 위험요인 & 완화

* 외부 API 변경/레이트리밋 → 백오프/캐시/폴백 메시지
* 일부 재무 필드 결측 → 지표 계산 시 None-safe 처리·대체 규칙
* LLM 환각 → 숫자는 툴만, 출처 강제, 근거 없으면 “정보 부족” 표시

---

## 8) 완료 정의 (Definition of Done)

* [ ] 모든 마일스톤 **[DONE]** 처리
* [ ] Streamlit 앱이 로컬에서 실행되어 E2E 시나리오 통과
* [ ] README/TASKS/AGENTS 및 샘플 리포트 업데이트
* [ ] 주요 함수 테스트 통과, 기본 로그/캐시 동작 확인

---

## 9) Codex 워크플로(예시)

> Codex CLI/IDE의 실제 명령은 팀의 세팅에 맞춰 조정하세요. 아래는 일반적인 흐름 예시입니다.

1. **/init** – 리포지토리 스캔 및 작업 준비
2. **/status** – 현재 진행 상태 확인(TASKS.md 체크박스/태그 기반)
3. **A-1 → A-2 → A-3 …** 순으로 태스크 단위로 실행
4. 각 태스크 완료 시 `TASKS.md` 상태를 `[DONE]`으로 갱신하고 커밋 메시지(`feat:`, `chore:`) 생성
5. 오류/누락 발견 시 즉시 하위 태스크를 Backlog에 추가하고 `[DOING]`으로 전환
6. Milestone D 완료 시 최초 E2E 데모, 피드백 반영 후 E-Tasks로 품질 보강
7. `/status`로 전체 진행률 보고 → 최종 **Definition of Done** 충족 시 종료

---

### 부록) AGENTS.md 초안(요약 템플릿)

```
# AGENTS

## Sector Researcher
- Input: sectors[], keywords[], k
- Tools: firecrawl.search/scrape
- Output: candidates[{ticker, name, source_urls[]}]
- Rules: 모든 주장에 출처 포함

## Ticker Screener
- Input: candidates[]
- Tools: yfinance.info/history
- Output: tradable_candidates[] (유동성/상장 체크)
- Rules: 결측/오류시 제외 사유 기록

## Fundamentals Fetcher
- Input: tradable_candidates[]
- Tools: yfinance, SEC (10-K/10-Q)
- Output: facts_by_ticker{…}
- Rules: 날짜/단위 명시, 근거 링크 유지

## Valuation Analyst
- Input: facts_by_ticker
- Output: scores[{ticker, score(0~1), rationale_md}]
- Rules: 수치=툴 결과만, 가중치/공식 명시

## Insider & Ownership Analyst
- Input: tickers[]
- Tools: SEC Form 4
- Output: insights[{ticker, bullets[], sources[]}]

## Risk Analyst
- Input: tickers[]
- Tools: yfinance.history, pandas
- Output: corr_matrix(df), risk_notes

## Allocator
- Input: candidates, constraints
- Output: weights{ticker: weight}
- Rules: 합=1, 제약 위반 0

## Trade Planner
- Input: weights, budget
- Output: orders[{ticker, qty, est_price, fee, total}]
- Rules: T+1/수수료/반올림 규칙
```
