# AGENTS

본 문서는 AI 멀티에이전트 주식 자동매매 앱의 에이전트 및 핵심 모듈 사양을 정의합니다.
Phase 1 MVP: 2 AI 에이전트 + 규칙 기반 모듈. Phase 3에서 필요 시 확장.

---

## 0. AI 모델 라우팅 (3-Tier + 교차 검증)

> 비용 효율적 의사결정을 위해 3단계 모델 티어를 사용하며,
> SMART 티어에서 2개 모델이 독립 분석 후 교차 검증하는 구조.

| Tier | 모델 | CLI 도구 | 용도 | 예상 비용 |
|------|------|----------|------|----------|
| **QUICK** | Gemini 2.5/3.0 Flash | Antigravity (Gemini CLI) | 데이터 수집, 뉴스 정규화, 모니터링 텍스트 | $0.10~0.40/1M tokens |
| **SMART-A** | Claude Haiku 4.5 | Claude Code | 분석 요약, 차트 패턴 인식, 데이터 정리 | $0.25~1.00/1M tokens |
| **SMART-B** | GPT 5.2 | Codex CLI | 독립 분석, 교차 검증 대상, 패턴 검증 | $2.00~3.00/1M tokens |
| **EXPERT** | Claude Opus 4.5 | Antigravity (Gemini CLI) | **매수/매도 최종 결정**, 고불확실성 판단 | $15.00/1M tokens |

### 교차 검증 흐름
```
1. QUICK (Gemini Flash) → 데이터 수집 + DB 저장
2. SMART-A (Haiku) → 독립 분석 → TradeIdea 후보 A
3. SMART-B (GPT 5.2) → 독립 분석 → TradeIdea 후보 B
4. 교차 비교:
   - 일치 → 합의된 TradeIdea 확정
   - 불일치 → EXPERT (Opus)에게 최종 판단 위임
5. EXPERT (Opus) → 매수/매도 최종 결정 (불일치 시 또는 고불확실 상황)
```

### CLI 도구별 역할
- **Antigravity (Gemini CLI)**: Gemini 모델 + Claude 모델 실행. 데이터 수집(QUICK) + 최종 결정(EXPERT) 담당.
- **Claude Code**: Claude Haiku 실행. 분석/정리(SMART-A) 담당. 폴백 역할.
- **Codex CLI**: GPT 모델 실행. 교차 검증(SMART-B) 담당. 기존 유지.

---

### 1. Analyst Agent
- **역할**: 시장 데이터 분석 + 매매 아이디어(TradeIdea) 생성
- **Input**: `MarketSnapshot[]` (Pydantic 검증 완료)
- **Output**: `TradeIdea[]` (symbol, side, confidence, horizon, entry, exit, thesis, constraints)
- **Tools**: `llm_client` (교차 검증 라우팅 via LiteLLM), `yfinance_client`, `firecrawl_client` (뉴스)
- **모델 사용 흐름**:
  1. 데이터 수집: QUICK (Gemini Flash) — 뉴스 크롤링, 시장 데이터 정규화, 하루 최대 2회
  2. 분석 A: SMART-A (Claude Haiku) — 수집 데이터 기반 TradeIdea 후보 생성
  3. 분석 B: SMART-B (GPT 5.2) — 동일 데이터로 독립 TradeIdea 후보 생성
  4. 교차 검증: 두 결과 비교 (일치 → 확정, 불일치 → EXPERT 위임)
  5. 최종 결정: EXPERT (Claude Opus) — 불일치 시 또는 고불확실 상황에서 매수/매도 결정
- **Rules**:
  - 구조화 출력 (function calling / tool use) 필수 — prompt-and-parse 금지
  - LLM 타임아웃 30초, 초과 시 HOLD 반환
  - 최대 2회 재시도 (exponential backoff)
  - 파싱 실패 → HOLD 폴백
  - AI 생성 텍스트(thesis)는 의사결정 흐름에 영향 없음 (로깅용)
  - 수치/열거형 필드만 Risk Gate/OMS 구동
  - 교차 검증 불일치 시 EXPERT 호출, 그래도 판단 불가면 HOLD
  - LiteLLM Virtual Key로 모델 접근/예산 격리 (OAuth 인증)
- **Phase**: Phase 1a (Week 3)

### 2. Monitor Agent
- **역할**: 보유 포지션 실시간 감시 + 이상 감지 + 자동 정지 트리거
- **Input**: `Portfolio` + `MarketSnapshot` (현재 포지션 + 최신 시세)
- **Output**: `Alert` (severity, message, action: HOLD|REDUCE|STOP)
- **Tools**: `yfinance_client` (가격 모니터링), `llm_client` (Gemini Flash — 이상 분석 텍스트, 선택적)
- **모델 사용**: QUICK (Gemini Flash)만 — 이상 감지 텍스트 생성 시에만 (선택적)
- **Rules**:
  - 급등락 감지: 단일 종목 ±{threshold}% 이내 {minutes}분 → Alert
  - 데이터 피드 중단 감지: {stale_minutes}분 이상 갱신 없음 → AutoStop
  - 포트폴리오 MDD 초과 → Kill Switch Level 1 트리거
  - LLM 없이도 규칙 기반으로 독립 동작 가능 (LLM 비용 없음)
  - LLM 사용 시: 이상 원인 분석 텍스트 생성 (로그용, 의사결정에 영향 없음)
- **Phase**: Phase 1b (Week 4)

### 3. Risk Gate (규칙 기반 모듈 — AI 아님)
- **역할**: TradeIdea → ApprovedOrderPlan 또는 Rejected 변환. 단일 관문(Single Choke Point).
- **Input**: `TradeIdea`, `Portfolio`, `RiskPolicy`
- **Output**: `ApprovedOrderPlan` (Phase 1a: config flag 승인) 또는 `Rejected` (reason)
- **하드 규칙**:
  1. 단일 종목 비중 ≤ max_position_pct (보수적 3%, 방어적 5%, 공격적 10%)
  2. 동시 보유 종목 수 ≤ max_positions (보수적 3, 방어적 5, 공격적 8)
  3. 포트폴리오 MDD 초과 시 신규 진입 중단
  4. 일일 손실 한도 초과 → 당일 거래 종료
  5. 일일 최대 주문 건수 초과 → 거부
  6. 마켓 시간 외 → 거부
  7. 데이터 신선도 미달 → 거부
  8. 10:00 AM KST 이전 → 거부 (오픈 변동성 회피, 매매 허용 시간: 10:00~15:00 KST)
  9. 데이터 수집 허용 시간: 08:30~15:00 KST
- **보안**:
  - Phase 1a: `TradingMode` config flag (PAUSED/PAPER/REAL)
  - Phase 1b+: HMAC 서명 capability_token (ApprovedOrderPlan 정준 해시 바인딩, trace_id+order+sizing+만료 60초+nonce, 1회 사용)
- **Phase**: Phase 1a (Week 2, 기본) → Phase 1b (Week 5, HMAC 강화)

### 4. Execution OMS (규칙 기반 모듈 — AI 아님)
- **역할**: ApprovedOrderPlan → 브로커 주문 실행 + 상태 추적
- **Input**: `ApprovedOrderPlan` (Phase 1b+: HMAC token 검증 필수)
- **Output**: `OrderResult` (broker_order_id, idempotency_key, status, fills, fees)
- **상태 머신**: NEW → SUBMITTED → PARTIAL → FILLED | CANCELED | REJECTED
- **규칙**:
  - 멱등성 키: UUID v4, SQLite 영속화
  - 중복 주문 방지: 동일 멱등성 키 재제출 시 기존 결과 반환
  - 브로커 client_order_id 활용
  - 글로벌 backoff + circuit breaker (5회 연속 실패 → 1분 대기)
  - SQLite WAL 모드, fsync 보장
  - crash recovery: 재시작 시 미확인 주문 상태 조회 → 재조정
- **Phase**: Phase 1a (Week 2, 기본) → Phase 1b (Week 4, 멱등성 강화)

### 5. 데이터 흐름 (Pipeline — 이벤트 드리븐)
```
[Scheduler] 데이터 수집 트리거 (08:30~15:00, 하루 최대 2회)
    ↓
[Data Hub — QUICK: Gemini Flash] → DB 저장
    ↓ (수집 완료 이벤트)
[Analyst Agent]
    ├─ SMART-A (Haiku): 독립 분석 → TradeIdea 후보 A
    ├─ SMART-B (GPT 5.2): 독립 분석 → TradeIdea 후보 B  (병렬)
    ├─ 교차 검증 (일치 → 확정, 불일치 → EXPERT)
    └─ EXPERT (Opus): 최종 매수/매도 결정 (필요 시)
    ↓ (TradeIdea 생성 즉시)
[Risk Gate — 규칙 기반, 8개 하드 규칙] ← 10:00~15:00 가드레일
    ↓
[OMS — SQLite WAL, 상태 머신] ← 매매 수시 실행
    ↓
[Broker Adapter — KIS 모의투자]
    ↓
[Audit Log]

[Monitor Agent — 규칙 기반 + QUICK] ← 보유 중 수시 감시 → 즉시 SELL 트리거
    ← Portfolio ← Reconciliation
```

### 6. 공통 규칙
- 모든 I/O는 Pydantic 스키마 검증 필수
- 수치/날짜/계산값은 도구(yfinance/브로커 API) 결과만 사용
- LLM 생성 텍스트는 제어 흐름에 영향 없음 (텍스트 격리)
- LLM 장애 시: HOLD (신규 진입 금지, 기존 손절만 유지)
- 앱 재시작 시: disarmed 상태, 재조정 완료 전 거래 금지
- 에이전트별 LiteLLM Virtual Key로 OAuth 인증, 모델 접근/예산 격리
- 교차 검증 불일치 + EXPERT 판단 불가 → HOLD (안전 기본값)
- 데이터 수집은 하루 최대 2회, DB에 저장 후 재활용
- 운영 시간: 데이터 수집 08:30~15:00 KST, 매매 10:00~15:00 KST
