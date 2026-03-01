# AI 멀티에이전트 주식 자동매매 데스크톱 앱 — 실행 계획서 v4

> **문서 목적**: 개인용 자동매매 앱을 구현하기 위한 실행 계획서. 기존 포트폴리오 리서치 앱의 도구 래퍼(yfinance, firecrawl, SEC, LLM)를 재활용하고 아키텍처를 완전 재설계.
> **리뷰 이력**: Oracle 2회 + Momus 2회 교차 검증 완료. 총 32개 이슈 반영.

---

## 0. 핵심 원칙

1. **AI = 조언자/검증자/감시자**, 최종 주문은 규칙 기반 Risk Gate만 실행 권한
2. **단일 관문(Single Choke Point)**: HMAC 서명된 capability token 없이는 OMS 접근 불가
3. 페이퍼 트레이딩과 실거래가 **동일 엔진** 사용 (어댑터만 교체)
4. **Kill Switch** 3단계: (1) 신호 중단, (2) 미체결 취소, (3) 전 포지션 청산 (가격 보호 포함)
5. **Crash-safe 기본값**: 앱 재시작 시 자동매매 OFF(disarmed), 브로커 재조정 완료 전 거래 금지
6. **LLM 실패 시 기본 행동**: HOLD (신규 진입 금지, 기존 손절만 유지)
7. **텍스트 격리 규칙**: AI 생성 텍스트(thesis, news_risk)는 의사결정에 영향 없음. 수치/열거형 필드만 Risk Gate/OMS 구동
8. **단계적 보안 강화**: Phase 1a는 단순한 config flag 기반, Phase 1b+에서 HMAC capability token으로 강화
9. **계층형 모델 전략**: 데이터 수집/모니터링은 저비용 모델(Gemini Flash), 의사결정은 고비용 모델(GPT-4o/Claude Sonnet) 사용
10. **OAuth 인증 통합**: LiteLLM Proxy + Virtual Keys로 에이전트별 인증/예산/모델 접근 제어
11. **데이터 수집 최소화**: 하루 최대 2회 수집 → DB 저장 → 재활용. 매번 API 호출하지 않음

---

## 1. 3단계 실행 전략

솔로 개발자 현실을 반영하여 3단계로 분리. 이전 단계가 안정 운영되어야 다음 단계 진입.

| Phase | 기간 | 목표 | 시장 | 진입 조건 |
|-------|------|------|------|----------|
| **Phase 1: KR Paper MVP** | 6주 | 한국 시장 KIS 모의투자 + CLI 출력 + 2 에이전트 | KR만 | — |
| **Phase 2: US Market Addition** | 6주 | US 시장 Alpaca 추가 + Streamlit 대시보드 + 백테스트 | KR+US | Phase 1 4주 무장애 운영 |
| **Phase 3: Real Trading** | 4+주 | 실거래 연동 + 모니터링 고도화 | KR+US | Phase 2 완료 + 보안 리뷰 + 백테스트 유효성 확인 |

---

## 2. 기술 스택

| 구분 | 선택 | 근거 |
|------|------|------|
| 언어 | Python 3.11+ | 기존 코드 재활용, 데이터 생태계 |
| LLM | GPT-4o / Claude Sonnet (의사결정, via LiteLLM) + Gemini 2.5 Flash / 3.0 Flash (데이터 수집/모니터링, via LiteLLM) | 계층형 모델 전략, 비용 최적화 |
| LLM Gateway | LiteLLM Proxy | 에이전트별 OAuth 인증, Virtual Keys, 예산 제한, 모델 접근 제어 |
| 오케스트레이션 | 직접 구현 (asyncio + 함수 파이프라인) | 에이전트 2개 수준에서 프레임워크 불필요 |
| UI (Phase 2) | Streamlit | 기존 경험, 빠른 프로토타입 |
| 데이터 (Phase 1) | yfinance (기존 래퍼 재활용) | 무료, 페이퍼 트레이딩에 충분 |
| 데이터 (Phase 3) | Polygon.io 또는 브로커 피드 | 실거래 안정성 |
| 브로커 (Phase 1) | KIS (KR, 모의투자) | 한국 시장 우선, OAuth 2.0 API |
| 브로커 (Phase 2) | Alpaca (US, 페이퍼) | REST API 깔끔, 무료 페이퍼 |
| 마켓 캘린더 | exchange_calendars | KR(KRX) 우선, US 추가, DST, 세션 경계 |
| 시크릿 관리 | OS keychain (keyring) + .env 폴백 | OS 수준 암호화 |
| DB | SQLite (WAL 모드, fsync 보장) | 경량, 단일 프로세스 |
| 형상관리 | Git + pre-commit | 기존 동일 |

> **KIS 특이사항**
> - 모의투자 vs 실거래: 별도 URL/앱키 (config에서 `KIS_APP_KEY` / `KIS_APP_SECRET` + `KIS_BASE_URL`로 분리)
> - OAuth 2.0 인증: access_token 발급 → 만료 시 자동 갱신
> - KRX 거래시간: 09:00-15:30 KST
> - 가격 제한폭: ±30%
> - 호가 단위 규칙 적용 필요

---

## 3. 폴더 구조

```
trader-desktop/
├── config/
│   ├── risk_policy.yaml          # 리스크 프로필 3종
│   ├── strategy.yaml             # 전략 파라미터
│   ├── app.yaml                  # 앱 설정
│   └── litellm_config.yaml       # LiteLLM Proxy 설정, 모델 라우팅, Virtual Keys
├── schemas/
│   ├── models.py                 # Pydantic 모델 6종
│   └── events.py                 # 이벤트 스키마
├── engine/
│   ├── data_hub.py               # 시세 수집 + 신선도 체크
│   ├── market_calendar.py        # exchange_calendars 래퍼
│   ├── strategy_hub.py           # 전략 인터페이스
│   ├── risk_gate.py              # 규칙 기반 리스크 관문 (HMAC capability token)
│   ├── execution_oms.py          # 주문 상태 머신 + 멱등성
│   ├── portfolio.py              # 포지션/PnL 추적
│   ├── reconciliation.py         # 브로커↔내부 재조정 (포지션+현금+미체결)
│   ├── kill_switch.py            # 3단계 Kill Switch + 워치독
│   ├── logger.py                 # HMAC 키 서명 + 해시 체인 JSONL
│   └── replay.py                 # 감사 로그 리플레이 + 무결성 검증
├── adapters/
│   ├── base.py                   # 추상 브로커 어댑터
│   ├── paper_adapter.py          # 시뮬레이션 (bid/ask, 슬리피지)
│   ├── kis_adapter.py            # KIS (KR) - Phase 1
│   └── alpaca_adapter.py         # Alpaca (US) - Phase 2
├── agents/
│   ├── pipeline.py               # 에이전트 파이프라인
│   ├── analyst_agent.py          # 분석+전략 에이전트
│   ├── monitor_agent.py          # 감시 에이전트
│   └── prompts/                  # 프롬프트 템플릿
├── tools/                        # 기존 코드 재활용
│   ├── yfinance_client.py
│   ├── firecrawl_client.py
│   ├── sec_client.py
│   ├── llm_client.py
│   └── utils.py
├── ui/                           # Phase 2
│   └── app.py
├── tests/
├── storage/
│   ├── orders.db                 # SQLite (WAL 모드)
│   └── logs.jsonl                # HMAC 서명 감사 로그
├── docs/
│   ├── architecture.md
│   └── runbook.md
├── .env.example
├── AGENTS.md
├── TASKS.md
└── README.md
```

---

## 4. 에이전트 설계

### Phase 1: 2개 에이전트 (MVP)

| 에이전트 | 역할 | 입력 | 출력 | Tools |
|---------|------|------|------|-------|
| **Analyst Agent** | 데이터 분석 + 매매 아이디어 생성 | MarketSnapshot[] | TradeIdea[] | yfinance_client, firecrawl_client, llm_client, **Gemini Flash (데이터 수집), GPT-4o/Claude Sonnet (분석/의사결정)** |
| **Monitor Agent** | 포지션 감시 + 이상 감지 + 자동 정지 | Portfolio + MarketSnapshot | Alert / AutoStop | yfinance_client, **규칙 기반 주력 (LLM 불필요), 이상 분석 시 Gemini Flash (로그용)** |

- Risk Gate = **규칙 기반 엔진 모듈** (AI 불필요)
- Execution = **함수 호출** (에이전트 불필요)
- Data 수집 = **Data Hub 함수** (에이전트 불필요)

### Phase 3: 확장 (필요 시)
- Tuning Agent 추가 (백테스트 기반 파라미터 제안, 승인 후 반영)

### 에이전트 보안 규칙
- 모든 I/O는 Pydantic 스키마 검증 필수
- 에이전트는 주문 실행 권한 없음 (TradeIdea만 생성)
- 시장 데이터는 structured fields로만 LLM에 전달 (raw text 금지)
- LLM 타임아웃 30초, 초과 시 HOLD
- AI 생성 텍스트(thesis 등)는 제어 흐름에 영향 금지 (텍스트 격리)
- **에이전트별 LiteLLM Virtual Key로 모델 접근/예산 격리**

---

## 5. 리스크 프로필 (RiskPolicy)

| 항목 | 보수적 | 방어적 | 공격적 |
|------|--------|--------|--------|
| 종목당 최대 비중 | 1-3% | 2-4% | 3-7% |
| 일일 손실 제한 | 1.0% | 1.5% | 2.0% |
| MDD 한도 | 5% | 7% | 10% |
| 거래 빈도 | 낮음 (EOD) | 중간 (EOD) | 높음 (일중 가능) |
| 변동성 필터 | 강함 | 매우 강함 | 중간 |
| 레버리지 | OFF | OFF | 선택(기본 OFF) |
| 시장 급변 시 | 즉시 정지 | 정지+현금↑ | 축소 또는 정지 |
| 일일 최대 주문 건수 | 3 | 5 | 10 |
| 신규 종목 진입 대기 | 3일 관찰 | 1일 관찰 | 즉시 |

### 포지션 사이징 규칙 (Risk Gate 하드 규칙)

1. 단일 종목 비중 ≤ {max_position_pct}% (보수적 3%, 방어적 5%, 공격적 10%)
2. 동시 보유 종목 수 ≤ {max_positions} (보수적 3, 방어적 5, 공격적 8)
3. 포트폴리오 MDD {max_drawdown_pct}% 초과 시 신규 진입 중단 (보수적 5%, 방어적 7%, 공격적 10%)
4. 마켓 오픈 딜레이: KRX 기준 10:00 AM KST 이전 주문 거부

---

## 5-1. 운영 스케줄 & 모델 라우팅

### 운영 시간 (KST 기준)

| 활동 | 시간 | 빈도 | 사용 모델 | CLI 도구 |
|------|------|------|----------|----------|
| 데이터 수집 (뉴스, 시장 데이터) | 08:30 ~ 15:00 | 하루 최대 2회 (장전 + 장중) | Gemini 2.5/3.0 Flash | Antigravity |
| 분석 A (요약/차트/정리) | 10:00 ~ 15:00 | 수집 데이터 기반 | Claude Haiku 4.5 | Claude Code |
| 분석 B (교차 검증) | 10:00 ~ 15:00 | 분석 A와 병렬 | GPT 5.2 | Codex CLI |
| 매수/매도 최종 결정 | 10:00 ~ 15:00 | 교차 검증 불일치 시 | Claude Opus 4.5 | Antigravity |
| 포지션 모니터링 | 보유 중 수시 | 규칙 기반 (LLM 불필요) | — | — |
| 이상 감지 텍스트 생성 | 이상 발생 시 | 선택적 | Gemini Flash | Antigravity |

### 데이터 수집 → 교차 검증 → 결정 흐름

```
[이벤트 드리븐 — 매매는 허용 시간 내 수시 실행]

[Scheduler] 데이터 수집 트리거 (08:30~15:00, 하루 최대 2회)
    ↓
[QUICK: Gemini Flash] 뉴스 + 시장 데이터 수집 → DB 저장
    ↓ (수집 완료 이벤트)
[SMART-A: Haiku] + [SMART-B: GPT 5.2] 병렬 독립 분석
    ↓
교차 검증: 일치 → TradeIdea 확정 / 불일치 → [EXPERT: Opus] 최종 판단
    ↓ (TradeIdea 생성 시 즉시)
[Risk Gate → OMS → 브로커] 매매 실행 (10:00~15:00 내 수시)

[Monitor Agent] 보유 중 수시 감시 → 조건 충족 시 즉시 SELL 트리거
```

### 계층형 모델 라우팅 (3-Tier + 교차 검증)

| Tier | 모델 | CLI 도구 | 용도 | 예상 비용 |
|------|------|----------|------|----------|
| QUICK | Gemini 2.5/3.0 Flash | Antigravity | 데이터 수집, 정규화, 분류, 모니터링 텍스트 | $0.10~0.40/1M tokens |
| SMART-A | Claude Haiku 4.5 | Claude Code | 분석 요약, 차트/패턴 인식, 데이터 정리 | $0.25~1.00/1M tokens |
| SMART-B | GPT 5.2 | Codex CLI | 독립 분석, 교차 검증, 패턴 검증 | $2.00~3.00/1M tokens |
| EXPERT | Claude Opus 4.5 | Antigravity | **매수/매도 최종 결정**, 불일치 해소, 고불확실성 | $15.00/1M tokens |

- Phase 1: Static Routing (config에서 에이전트별 모델 고정 + 교차 검증 로직)
- Phase 2+: Cascade Pattern (SMART 교차 검증 → 불일치 시 자동 EXPERT 에스컬레이션)

### CLI 도구별 역할

| CLI 도구 | 제공 모델 | 역할 |
|----------|----------|------|
| **Antigravity** (Gemini CLI) | Gemini Flash, Claude Opus | 데이터 수집(QUICK) + 최종 결정(EXPERT) |
| **Claude Code** | Claude Haiku | 분석/정리(SMART-A). 폴백 역할 |
| **Codex CLI** | GPT 5.2 | 교차 검증(SMART-B) |

### LiteLLM Proxy 구성

- 에이전트별 Virtual Key 발급 (예산/모델 접근 제한)
- Analyst Agent: SMART-A + SMART-B + EXPERT 모델 접근, 일일 $5 한도
- Monitor Agent: QUICK 모델만, 일일 $0.50 한도
- 교차 검증 불일치 + EXPERT 판단 불가 → HOLD (안전 기본값)
- 폴백 체인: EXPERT → SMART-A → QUICK (의사결정은 절대 QUICK으로 폴백 안 함)
---

## 6. JSON 스키마 (Pydantic 모델)

1. **MarketSnapshot**: trace_id, ts, venue(KR|US), symbol, price, bid, ask, volume, candle, features(volatility, trend, news_risk)
2. **TradeIdea**: trace_id, symbol, side(BUY|SELL), confidence(0~1), horizon, thesis, entry, exit(TP/SL), constraints
3. **ApprovedOrderPlan / Rejected**: trace_id, decision, mode, reason, sizing, risk_checks, order, **capability_token** (HMAC 서명, ApprovedOrderPlan 해시에 바인딩)
4. **OrderResult**: trace_id, broker_order_id, **idempotency_key**, status, fills[], fees, message
5. **ReconciliationResult**: timestamp, broker_positions, broker_cash, broker_open_orders, internal_positions, internal_cash, mismatches[], resolution
6. **ConfigChangeEvent**: timestamp, changed_by, old_value, new_value, version_tag, approval_log

---

## 7. Phase 1 — KR Paper MVP (Week 1-6)

### Phase 1a: Working Paper Trader (Week 1-3)
> 목표: 최대한 빠르게 E2E KR 페이퍼 트레이딩 루프 구동 (ASAP)

#### Milestone A: 기반 세팅 (Week 1)

- **A-1 [DONE] 프로젝트 스캐폴딩**
  - 수행: 기존 tools/ 복사, 신규 디렉토리 생성, .env.example 업데이트
  - 수용기준: `tree` 출력이 §3 구조와 일치
  - 검증: `python -c "from tools import yfinance_client"` 성공

- **A-2 [DONE] Pydantic 스키마 정의 (schemas/models.py)**
  - 수행: 6개 모델 정의 (§6), capability_token HMAC 필드, idempotency_key 포함
  - 수용기준: 모든 모델 직렬화/역직렬화 테스트 통과
  - 검증: `pytest tests/test_schemas.py`

- **A-3 [DONE] 설정 시스템 (config/)**
  - 수행: risk_policy.yaml, strategy.yaml, app.yaml, **litellm_config.yaml** + Pydantic BaseSettings 검증
  - 수용기준: 잘못된 config → ValidationError
  - 검증: `pytest tests/test_config.py`

- **A-4 [DONE] 감사 로깅 시스템 — Phase 1a (engine/logger.py)**
  - 수행: Append-only JSONL + fsync + 일일 체크섬 (Phase 1a 버전, 해시 체인 미포함)
  - 수용기준: 로그 정상 기록 및 체크섬 검증 가능
  - 검증: `pytest tests/test_logger.py`
  - ⚠️ **Phase 1b에서 강화**: HMAC 키 서명(keychain) + 해시 체인 + 시퀀스 번호 → `pytest tests/test_logger_integrity.py`

- **A-5 [DONE] 시크릿 관리**
  - 수행: keyring OS 키체인 + .env 폴백 (파일 권한 600 강제, prod 모드에서 .env 금지) + 로그 레닥션 + 알림 채널 레닥션
  - 수용기준: `grep -r "sk-" logs.jsonl` 결과 0건, .env 파일 권한 600
  - 검증: `pytest tests/test_secret_redaction.py`

#### Milestone B: 코어 엔진 (Week 2)

- **B-1 [DONE] 마켓 캘린더 (engine/market_calendar.py)**
  - 수행: exchange_calendars 래핑, KRX 세션/휴일 우선, US+DST 추가
  - 수용기준: KRX 거래시간 (09:00-15:30 KST) + DST 전환일 정확
  - 검증: `pytest tests/test_market_calendar.py`

- **B-2 [DONE] 포트폴리오 모듈 (engine/portfolio.py)**
  - 수행: 포지션 추적 (인-메모리), PnL(수수료 반영), 일일 리셋(마켓 캘린더 기준)
  - 수용기준: 매수→매도 PnL 정확 계산
  - 검증: `pytest tests/test_portfolio.py`

- **B-3 [DONE] Risk Gate — Phase 1a (engine/risk_gate.py) — CRITICAL**
  - 수행: config flag 기반 TradingMode enum (PAUSED/PAPER/REAL) + 3 하드 규칙 (일일 손실, MDD, 집중도), 데이터 신선도, 주문 건수, 마켓 시간
  - 수용기준: PAUSED 모드에서 모든 주문 차단, 하드 규칙 위반 시 거부
  - 검증: `pytest tests/test_risk_gate.py` (20+ 케이스)
  - ⚠️ **Phase 1b에서 강화**: HMAC capability_token (ApprovedOrderPlan 정준 해시 바인딩, trace_id+order+sizing+만료+nonce), 1회 사용 후 무효화

- **B-4 [TODO] Execution OMS — Phase 1a (engine/execution_oms.py) — CRITICAL**
  - 수행: 상태 머신(NEW→SUBMITTED→PARTIAL→FILLED|CANCELED|REJECTED), 주문 전송 + 로그, SQLite WAL 모드 영속화
  - 수용기준: 재시작 후 상태 복원
  - 검증: `pytest tests/test_oms.py`
  - ⚠️ **Phase 1b에서 강화**: 멱등성 키, HMAC token 검증+해시 매칭, 글로벌 backoff + circuit breaker

- **B-7 [TODO] Strategy Hub (engine/strategy_hub.py)**
  - 수행: 전략 인터페이스, 기본 이동평균 크로스오버 (LLM 불필요), LLM 전략 연결
  - 수용기준: 더미 데이터로 TradeIdea 생성
  - 검증: `pytest tests/test_strategy_hub.py`

#### Milestone C: 기본 파이프라인 & 첫 페이퍼 거래 (Week 3)

- **C-3 [TODO] KIS 어댑터 (adapters/kis_adapter.py)**
  - 수행: KIS 모의투자 API 직접 연동, OAuth 2.0 access_token 발급 + 자동 갱신, KRX 거래시간 검증
  - 수용기준: 모의투자 환경 주문 제출 → 체결 확인
  - 검증: `pytest tests/test_kis_adapter.py`

- **C-5 [TODO] Analyst Agent (agents/analyst_agent.py)**
  - 수행: LLM 호출 → TradeIdea[], 구조화 출력, Pydantic 검증, 프롬프트 인젝션 방지
  - 모델 티어: 데이터 수집 단계 Gemini Flash (QUICK Tier), 분석/의사결정 GPT-4o/Claude Sonnet (SMART Tier)
  - 수용기준: 유효 TradeIdea 생성 + 무효 응답 graceful fallback
  - 검증: `pytest tests/test_analyst_agent.py`

- **C-4 [TODO] 에이전트 파이프라인 (agents/pipeline.py)**
  - 수행: Scheduler → Data Hub → Analyst Agent → Risk Gate → OMS → Log, EOD 배치 기본
  - 운영 시간: 데이터 수집 08:30-15:00 KST (하루 최대 2회), 매매 10:00-15:00 KST
  - 수용기준: 더미 데이터 E2E 완주, 파이프라인 60초 이내
  - 검증: `pytest tests/test_pipeline_e2e.py`

- **C-4a [TODO] 첫 페이퍼 거래 실행**
  - 수행: 실제 yfinance → Analyst → Risk Gate → KIS 모의투자 → 로그 확인
  - 수용기준: 최소 1건 KR 모의투자 주문 체결 + 로그 기록 확인

---

### Phase 1b: Hardening (Week 4-6)
> 목표: 멱등성, 보안, 견고성 확보

#### Milestone D: 강화 (Week 4)

- **B-4b [TODO] OMS 멱등성 강화**
  - 수행: 멱등성 키, 브로커 client_order_id 활용, 글로벌 backoff + circuit breaker 추가
  - 수용기준: 중복 방지, 레이트리밋 시 재시도 폭주 없음
  - 검증: `pytest tests/test_oms.py` (멱등성/circuit breaker 케이스 추가)

- **B-5 [TODO] 재조정 엔진 (engine/reconciliation.py)**
  - 수행: 시작 시 + 5분 주기 재조정, 범위: 포지션 + 현금/매수력 + 미체결 주문 + 체결 커서, 불일치 → 거래 동결
  - 수용기준: 의도적 불일치 시 자동 정지
  - 검증: `pytest tests/test_reconciliation.py`

- **C-6 [TODO] Monitor Agent (agents/monitor_agent.py)**
  - 수행: 포지션 감시, 급등락/피드 중단 감지, 자동 정지
  - 수용기준: 급락 시나리오 자동 정지 발동
  - 검증: `pytest tests/test_monitor_agent.py`

#### Milestone E: 보안 강화 (Week 5)

- **B-6 [TODO] Kill Switch — Level 1 (engine/kill_switch.py)**
  - 수행: Level 1(PAUSE = 신호 중단), crash-safe disarmed 기본값
  - 수용기준: PAUSE 시 신규 신호 완전 차단
  - 검증: `pytest tests/test_kill_switch.py`
  - ⚠️ Week 6에서 Level 2/3 + 워치독 추가

- **B-3b [TODO] Risk Gate HMAC capability token 강화**
  - 수행: HMAC 서명 capability_token (ApprovedOrderPlan 정준 해시 바인딩, trace_id+order+sizing+만료+nonce), 1회 사용 후 무효화, OMS HMAC token 검증+해시 매칭
  - 수용기준: 위조 token 거부, 만료 거부, payload 변조 거부, 이중 사용 거부
  - 검증: `pytest tests/test_risk_gate.py` (HMAC 케이스 추가)

- **A-4b [TODO] 감사 로그 HMAC 서명 강화 (engine/logger.py)**
  - 수행: HMAC 키 서명(keychain 시크릿) + 해시 체인 + 시퀀스 번호 추가
  - 수용기준: 로그 위변조 시 무결성 검증 실패, 전체 재작성 공격 탐지
  - 검증: `pytest tests/test_logger_integrity.py`

- **C-1 [TODO] 추상 브로커 어댑터 (adapters/base.py)**
  - 수행: ABC(connect, get_balance, get_positions, submit_order, cancel_order, get_order_status, get_fills), 타임아웃+재시도
  - 검증: mypy 통과

- **C-2 [TODO] 페이퍼 어댑터 (adapters/paper_adapter.py)**
  - 수행: Bid/Ask 기반, 슬리피지/수수료, 체결 지연, 마켓 시간 검증, Deterministic 모드
  - 수용기준: 동일 시드 → 동일 결과
  - 검증: `pytest tests/test_paper_adapter.py`

#### Milestone F: E2E 안정화 (Week 6)

- **D-1 [TODO] KR 페이퍼 트레이딩 E2E**
  - 수행: 실제 yfinance → Analyst → Risk Gate → KIS 모의투자 어댑터 → 로그, 2주 운영
  - 수용기준: 크래시 없이 2주, 감사 로그 무결성 유지
  - 검증: 일일 수동 확인 + 무결성 스크립트

- **D-2 [TODO] 성능 리포트**
  - 수행: Sharpe, MDD, 승률, Markdown 자동 생성
  - 검증: 리포트 수동 확인

- **B-6b [TODO] Kill Switch 완성 (Level 2/3 + 워치독)**
  - 수행: Level 2(CANCEL), Level 3(FLATTEN + 최대 스프레드 가드 + 장 외 시 보류), 워치독
  - 수용기준: kill -9 후 워치독 Level 2 실행, Level 3에서 wide spread 시 시장가 차단
  - 검증: `pytest tests/test_kill_switch.py`

- **D-3 [TODO] Phase 1 안정화**
  - 수행: 버그 수정, 엣지 케이스 보강
  - 수용기준: `pytest` 전체 통과 + 2주 무장애
  - 검증: `pytest -q` 전체 통과

---

## 8. Phase 2 — US Market Addition (Week 7-12)

> **사전 준비**: Alpaca API 계정 신청은 Week 1에 시작 (즉시 발급)

### Week 7-9: Backtest + Streamlit Dashboard

- **G-1 [TODO] 백테스트** — backtesting.py/vectorbt 활용 (직접 구현 금지)
- **E-1 [TODO] Streamlit 대시보드** — 계좌 요약, ON/OFF, 프로필 선택, Kill Switch 3단계
- **E-2 [TODO] 실시간 로그 뷰어** — 파이프라인 시각화
- **E-3 [TODO] 리스크 대시보드** — 일일 손실, MDD, 집중도

### Week 10-12: Alpaca Adapter + US Market

- **F-1 [TODO] Alpaca 어댑터 (adapters/alpaca_adapter.py)** — Alpaca 내장 페이퍼 트레이딩 API 직접 연동 (커스텀 fill 시뮬레이션 없음, Alpaca가 처리)
- **F-2 [TODO] US 마켓 특화** — DST 전환, EST 세션, US 마켓 캘린더 추가 (exchange_calendars)

> **Alpaca 특이사항**
> - 페이퍼 vs 실거래: 별도 URL/API 키 (config에서 `ALPACA_BASE_URL` / `ALPACA_API_KEY`로 분리 관리)
> - 무료 IEX 데이터: 15분 지연, EOD 배치에 충분. 일중 손절에는 유료 피드 필요 (Phase 3 검토)
> - 프랙셔널 주식: Phase 2 MVP에서 사용 (시장가만), Phase 3에서 정수 주식+지정가 전환 검토

---

## 9. Phase 3 — Real Trading (Week 13+)

### 진입 게이트
- [ ] Phase 1 KR 페이퍼 4주 무장애 운영
- [ ] Phase 2 US 통합 완료
- [ ] 백테스트 전략 유효성 확인
- [ ] 보안 리뷰 완료 (출금 권한 없음 런타임 자동 검증)

### 태스크
- **H-1 [TODO] 실거래 전환 안전장치** — Shadow mode, 단계적 노출($100→$500→$1000), 수동 arming(비밀번호), 3일 연속 손실 시 자동 rollback, 프리플라이트 체크
- **H-2 [TODO] 브로커 권한 검증** — 시작 시 + arming 시 출금 권한 없음 자동 체크 (API 지원 시), 미지원 시 수동 증명 + attested flag
- **H-3 [TODO] 운영 모니터링** — Telegram/이메일 알림 (레닥션 적용), 일일 리포트, 헬스체크

---

## 10. 테스트 전략

### 필수 테스트 (Phase 1 완료 전)
- [ ] Risk Gate: 20+ 케이스 (HMAC token, payload 변조, 만료, nonce 재사용)
- [ ] OMS: 멱등성, 상태 머신, 경쟁 조건, crash recovery, 레이트리밋 backoff
- [ ] 재조정: 포지션+현금+미체결+체결 커서, 불일치/허용 범위 경계
- [ ] Kill Switch: 3단계, 워치독, crash-safe, Level 3 wide-spread guard
- [ ] 감사 로그: HMAC 무결성, 전체 재작성 탐지, 시퀀스 검증
- [ ] Paper Adapter: 결정적 결과, 슬리피지/수수료
- [ ] E2E 파이프라인: 더미 + yfinance 실데이터

### 엣지 케이스 테스트
- [ ] Token replay + payload 변조 → OMS 거부
- [ ] 전체 로그 재작성 → HMAC 외부 앵커 검증 실패
- [ ] 네트워크 타임아웃 후 late fill → 재조정 + 포트폴리오 업데이트
- [ ] 멱등성 키 중복 재시도 → 이중 주문 방지
- [ ] Partial fill + cancel race → 상태 일관성
- [ ] 장 외/중단/경매 주문 → Risk Gate 거부
- [ ] 브로커 429 → 글로벌 backoff, 중복 submit 없음
- [ ] 기업 액션(split) → 재조정 불일치 감지
- [ ] DST 전환 → 일일 손실 한도 정확 리셋
- [ ] 앱 crash → disarmed + 재조정 후 거래 가능
- [ ] SQLite 전원 손실 → WAL 복구 + 미확인 주문 재조정
- [ ] 알림 내 예외 트레이스 → API 키 미노출

---

## 11. 운영 규칙 (Runbook)

1. 실계좌 전: 백테스트 → 페이퍼(4주+) → shadow(1주) → 소액 실거래
2. 데이터 지연/결측/오류 → 즉시 AutoStop + 알림
3. 연속 주문 실패 3회 → AutoStop
4. 일일 손실 한도 초과 → 당일 자동매매 종료
5. 전략/리스크 설정 변경 → 버전 태깅 + 변경 이력 + rollback 가능
6. LLM API 장애 → HOLD (신규 진입 금지)
7. 앱 재시작 → disarmed, 재조정 완료 전 거래 금지
8. API 키 → 출금 권한 없음 확인 (런타임 자동 검증), 키체인 저장, 로그/알림 레닥션
9. .env 파일 → 권한 600 강제, APP_ENV=prod 시 사용 금지
10. Kill Switch Level 3 → wide spread 시 시장가 차단, 장 외 시 보류
11. 프리마켓 체크: 장 시작 30분 전 오버나이트 뉴스 확인 (firecrawl), 중대 이벤트 감지 시 당일 HOLD

---

## 12. 완료 정의 (Definition of Done)

### Phase 1
- [ ] KR 페이퍼 4주 무장애 운영
- [ ] 감사 로그 HMAC 무결성 검증 통과
- [ ] Risk Gate 20+ 테스트 (HMAC token 바인딩 포함)
- [ ] OMS 멱등성/재조정/circuit breaker 테스트
- [ ] Kill Switch 3단계 + 워치독 + 가격 보호 테스트
- [ ] `pytest` 전체 통과
- [ ] 페이퍼 P&L이 백테스트 예측 대비 ±20% 이내 (전략 일관성 검증)
- [ ] 재조정 체크 100% 통과 (포지션 불일치 0건)
- [ ] Kill Switch 수동 테스트 완료 (전 레벨)
- [ ] LLM 장애 시나리오 테스트 (네트워크 차단 → HOLD 동작 확인)

### Phase 2
- [ ] Streamlit UI + Kill Switch 동작
- [ ] US 페이퍼 트레이딩 정상 동작 (Alpaca)
- [ ] 백테스트 결과 문서화

### Phase 3
- [ ] Shadow 1주 + 소액 실거래 정상 체결
- [ ] 브로커 권한 자동 검증 통과
- [ ] 일일 리포트 자동 발송
- [ ] Runbook 완성

---

## 13. 위험요인 & 완화

| 위험 | 완화 |
|------|------|
| 외부 API 변경/레이트리밋 | 백오프/캐시/폴백, 유료 데이터 소스 대비 |
| LLM 환각 | Risk Gate 규칙 필터, 수치=도구만, 구조화 출력, 텍스트 격리 |
| LLM API 장애 | HOLD 폴백, 기본 규칙 전략 |
| 브로커 API 장애 | 자동 정지, 재조정, 수동 개입 경로 |
| 네트워크 실패 중 주문 | 멱등성 키, client_order_id, 재조정, 미확인 동결 |
| 기업 액션(split) → 재조정 불일치 감지 → 수동 해결 |
| 시간대 오류 (DST) | exchange_calendars, UTC 내부 저장 |
| 프롬프트 인젝션 | 구조화 데이터만 LLM 입력, 텍스트 격리 |
| capability token 탈취 | HMAC 서명 + payload 바인딩 + 60초 만료 + nonce |
| 감사 로그 전체 재작성 | HMAC 키 서명 (키체인), 외부 체크포인트 |
| Kill Switch 급변 시장 | Level 3 wide-spread guard, 장 외 보류 |
| .env 시크릿 유출 | 파일 권한 600, prod 금지, 로그/알림 레닥션 |
| 브로커 출금 권한 | 런타임 자동 검증, attested flag, 키 변경 시 재확인 |

---

## 14. 리뷰 이력

| 라운드 | 리뷰어 | 이슈 수 | 주요 반영 사항 |
|--------|--------|---------|---------------|
| R1 | Oracle | 4 Critical + 8 Major | Risk Gate 단일 관문, OMS 멱등성, 재조정, Kill Switch 페일세이프, 감사 로그 해시 체인 |
| R1 | Momus | 8 High | 6→2 에이전트, 3단계 Phase, US 우선, 기존 코드 재활용, 현실적 타임라인 |
| R2 | Oracle | 2 Critical + 6 Major + 2 Minor | HMAC 토큰 바인딩, HMAC 로그 서명, 재조정 범위 확대, Kill Switch 가격 보호, OMS circuit breaker, SQLite WAL, .env 보안, 텍스트 격리 |
| R2 | Momus | 5 High + 5 Medium | Phase 1a/1b 분할, 과설계 단계화(token→config flag, hash-chain→JSONL), 포지션사이징 정의, 백테스트↔KIS 순서 교체, 게이트 성능기준, Alpaca 특이사항, 프리마켓 체크, 마켓오픈 딜레이 |
