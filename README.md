# AI 멀티에이전트 주식 자동매매 데스크톱 앱

개인용 AI 기반 주식 자동매매 시스템. 2개 AI 에이전트(분석+감시)와 규칙 기반 리스크 관문을 통해 안전한 자동 거래를 수행합니다.

## 핵심 원칙

- **AI = 조언자**: LLM은 분석/제안만, 최종 주문은 규칙 기반 Risk Gate가 결정
- **안전 우선**: Kill Switch 3단계, crash-safe 기본값(재시작 시 자동매매 OFF)
- **점진적 전환**: 백테스트 → 페이퍼(4주+) → shadow → 소액 실거래
- **LLM 장애 대응**: LLM 불가 시 HOLD (신규 진입 금지)
- **비용 효율**: 데이터 수집은 저비용 모델(Gemini Flash, 하루 2회), 의사결정만 고비용 모델 사용
- **OAuth 통합**: LiteLLM Proxy로 에이전트별 인증/예산/모델 접근 제어

## 아키텍처

```
[Scheduler] → [Data Hub (Gemini Flash)] → [Analyst Agent (GPT-4o/Sonnet)] → [Risk Gate] → [OMS] → [Broker]
                      ↓                                                                              ↑
                  [DB 저장]                                                                           │
                                                                                                      │
[Monitor Agent (규칙 기반)] ← [Portfolio] ← [Reconciliation] ← ──────────────────────────────────────┘
                      ↓
              [LiteLLM Proxy (OAuth)]
```

| 구성요소 | 역할 |
|---------|------|
| Analyst Agent | 시장 데이터 분석, 매매 아이디어(TradeIdea) 생성 |
| Monitor Agent | 포지션 감시, 이상 감지, 자동 정지 |
| Risk Gate | 규칙 기반 리스크 필터 (AI 아님) |
| OMS | 주문 실행 + 상태 추적 (멱등성 보장) |

## 기술 스택

| 구분 | 선택 |
|------|------|
| 언어 | Python 3.11+ |
| LLM (의사결정) | GPT-4o / Claude Sonnet (via LiteLLM) |
| LLM (데이터 수집) | Gemini 2.5/3.0 Flash (via LiteLLM) |
| LLM Gateway | LiteLLM Proxy (OAuth, Virtual Keys, 예산 제한) |
| 브로커 (KR) | KIS 한국투자증권 (Phase 1) |
| 브로커 (US) | Alpaca (Phase 2) |
| 데이터 | yfinance (Phase 1), Polygon.io (Phase 3) |
| UI | Streamlit (Phase 2) |
| DB | SQLite (WAL 모드) |
| 시크릿 | OS keychain (keyring) |

## 3단계 개발 전략

| Phase | 기간 | 목표 |
|-------|------|------|
| Phase 1: KR Paper MVP | 6주 | KR KIS 모의투자, CLI 출력, 2 에이전트 |
| Phase 2: US & UI | 6주 | US Alpaca 추가, Streamlit 대시보드, 백테스트 |
| Phase 3: Real Trading | 4+주 | 실거래 연동, 모니터링 고도화 |

## 빠른 시작

1. Python 환경 생성
   ```bash
   uv venv && . .venv/bin/activate
   ```

2. 패키지 설치
   ```bash
   uv pip install -r requirements.txt
   ```

3. 환경 변수 설정
   ```bash
   cp .env.example .env
   # .env 파일에 API 키 입력 (또는 keyring으로 OS 키체인에 저장)
   ```
   - `OPENAI_API_KEY` 또는 `ANTHROPIC_API_KEY`: LLM 의사결정 호출용
   - `GOOGLE_API_KEY`: Gemini Flash 데이터 수집 호출용
   - `KIS_APP_KEY`, `KIS_APP_SECRET`: KIS 브로커 (모의투자)
   - `KIS_BASE_URL`: 모의투자 = `https://openapivts.koreainvestment.com:29443`, 실거래 = `https://openapi.koreainvestment.com:9443`
   - `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`: Alpaca 브로커 (Phase 2에서 필요)
   - `ALPACA_BASE_URL`: 페이퍼 = `https://paper-api.alpaca.markets`, 실거래 = `https://api.alpaca.markets` (Phase 2에서 필요)
   - `LITELLM_MASTER_KEY`: LiteLLM Proxy 마스터 키 (선택, Phase 2+)

4. 페이퍼 트레이딩 실행 (Phase 1 완성 후)
   ```bash
   uv run python -m engine.main
   ```

5. UI 실행 (Phase 2 완성 후)
   ```bash
   uv run streamlit run ui/app.py
   ```

6. 테스트
   ```bash
   uv run pytest -q
   ```

## 프로젝트 구조

```
trader-desktop/
├── config/           # 리스크 정책, 전략, 앱 설정 (YAML); litellm_config.yaml 포함
├── schemas/          # Pydantic 모델 (MarketSnapshot, TradeIdea 등)
├── engine/           # 코어 엔진 (Risk Gate, OMS, Portfolio, Kill Switch 등)
├── adapters/         # 브로커 어댑터 (Paper, Alpaca, KIS)
├── agents/           # AI 에이전트 (Analyst, Monitor) + 파이프라인
├── tools/            # 데이터 래퍼 (yfinance, firecrawl, SEC, LLM)
├── ui/               # Streamlit 대시보드 (Phase 2)
├── tests/            # 테스트 스위트
├── storage/          # SQLite DB + 감사 로그
└── docs/             # 아키텍처 문서 + 운영 매뉴얼
```

## 문서

- `TASKS.md` — 실행 계획서 (Phase별 태스크 + 완료 기준)
- `AGENTS.md` — 에이전트 및 모듈 사양
- `docs/architecture.md` — 아키텍처 상세 (구현 시 작성)
- `docs/runbook.md` — 운영 매뉴얼 (구현 시 작성)

## ⚠️ 주의/면책

- 본 앱은 **개인 학습/실험 목적**이며, 투자 조언을 제공하지 않습니다.
- 자동매매는 **원금 손실 위험**이 있습니다. 감당 가능한 금액만 사용하세요.
- 데이터의 정확성을 보장하지 않으며, 외부 API 장애 시 예기치 않은 동작이 발생할 수 있습니다.
- 실거래 전 반드시 충분한 페이퍼 트레이딩 기간을 거치세요.
- 브로커 API 키는 **출금 권한 없이** 설정하세요.
