"""
schemas/models.py - Pydantic v2 모델 정의.

주요 모델:
- MarketSnapshot: 시장 스냅샷 (가격, 캔들, 특성)
- TradeIdea: 분석 에이전트의 매매 아이디어
- ApprovedOrderPlan: 리스크 게이트 승인 주문 계획
- Rejected: 리스크 게이트 거부
- OrderResult: OMS 주문 실행 결과
- ReconciliationResult: 재조정 결과
- Portfolio: 포트폴리오 상태
- RiskPolicy: 리스크 정책

모든 datetime은 UTC timezone-aware.
모든 금액은 KRW (KR venue) 또는 USD (US venue).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# ENUMS (7)
# ============================================================================


class Venue(str, Enum):
    """거래소 구분."""

    KR = "KR"  # 한국 KRX
    US = "US"  # 미국 NASDAQ/NYSE


class Side(str, Enum):
    """매매 방향."""

    BUY = "BUY"
    SELL = "SELL"


class TradingMode(str, Enum):
    """자동매매 모드."""

    PAUSED = "PAUSED"  # 일시 중지
    PAPER = "PAPER"  # 모의투자
    REAL = "REAL"  # 실거래


class OrderStatus(str, Enum):
    """주문 상태."""

    NEW = "NEW"  # 신규
    SUBMITTED = "SUBMITTED"  # 제출됨
    PARTIAL = "PARTIAL"  # 부분 체결
    FILLED = "FILLED"  # 완전 체결
    CANCELED = "CANCELED"  # 취소됨
    REJECTED = "REJECTED"  # 거부됨


class Horizon(str, Enum):
    """매매 기간."""

    INTRADAY = "INTRADAY"  # 당일
    SWING = "SWING"  # 스윙 (수일~수주)
    POSITION = "POSITION"  # 포지션 (수주~수개월)


class AlertSeverity(str, Enum):
    """알림 심각도."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertAction(str, Enum):
    """알림 권장 조치."""

    HOLD = "HOLD"  # 유지
    REDUCE = "REDUCE"  # 감소
    STOP = "STOP"  # 정지


# ============================================================================
# NESTED MODELS
# ============================================================================


class Candle(BaseModel):
    """OHLCV 캔들 데이터."""

    open: float
    """시가."""
    high: float
    """고가."""
    low: float
    """저가."""
    close: float
    """종가."""
    volume: int
    """거래량 (주 단위)."""


class Features(BaseModel):
    """마켓 특성 지표. Analyst Agent 구현 시 필드 확장 예정."""

    volatility: float | None = None
    """변동성 (0~1)."""
    trend: float | None = None
    """추세 지표 (-1~1)."""
    news_risk: float | None = None
    """뉴스 리스크 점수 (0~1). 텍스트 격리: 수치만, 의사결정 흐름에 영향 없음."""


class Position(BaseModel):
    """개별 포지션. qty는 주(株) 단위 정수."""

    symbol: str
    """종목 코드 (예: 005930 for 삼성전자)."""
    qty: int
    """보유 수량 (주 단위, 정수만). KRX: 주 단위, 정수만."""
    avg_price: float
    """평균 매입가 (KRW 기준). KRX 호가 단위 보정은 OMS 책임."""
    current_price: float = 0.0
    """현재가 (KRW 기준)."""
    unrealized_pnl: float = 0.0
    """미실현 손익 (KRW)."""
    realized_pnl: float = 0.0
    """실현 손익 (KRW)."""


class Fill(BaseModel):
    """개별 체결 내역."""

    fill_id: str
    """체결 ID (브로커 제공)."""
    qty: int
    """체결 수량 (주 단위)."""
    price: float
    """체결가 (KRW)."""
    timestamp: datetime
    """체결 시각 (UTC timezone-aware)."""
    fee: float = 0.0
    """수수료 (KRW)."""


class OrderSizing(BaseModel):
    """주문 사이즈. qty는 주 단위 정수."""

    qty: int
    """주문 수량 (주 단위, 정수만)."""
    notional: float
    """주문 예상 금액 (KRW)."""
    weight_pct: float
    """포트폴리오 비중 (%)."""


class RiskCheckResult(BaseModel):
    """개별 리스크 규칙 점검 결과."""

    rule_name: str
    """규칙 이름 (예: 'max_position_pct')."""
    passed: bool
    """통과 여부."""
    detail: str = ""
    """상세 메시지."""


class Mismatch(BaseModel):
    """재조정 시 발견된 불일치 항목."""

    field: str
    """불일치 필드명."""
    broker_value: str
    """브로커 값."""
    internal_value: str
    """내부 값."""
    severity: AlertSeverity = AlertSeverity.MEDIUM
    """심각도."""


class BrokerOrder(BaseModel):
    """브로커에 전달할 주문 정보. KRX 호가 단위 보정은 OMS/어댑터 책임."""

    symbol: str
    """종목 코드."""
    side: Side
    """매매 방향 (BUY/SELL)."""
    qty: int
    """주문 수량 (주 단위, 정수만)."""
    price: float | None = None
    """주문가 (KRW). None = 시장가."""
    order_type: str = "LIMIT"
    """주문 유형 (LIMIT, MARKET)."""
    time_in_force: str = "GTC"
    """유효 기간 (GTC=Good-Till-Cancel, DAY, IOC)."""


# ============================================================================
# MAIN MODELS (6)
# ============================================================================


class MarketSnapshot(BaseModel):
    """시장 스냅샷. 특정 시점의 종목 가격 및 특성."""

    trace_id: UUID = Field(default_factory=uuid4)
    """추적 ID (UUID v4)."""
    ts: datetime
    """스냅샷 시각 (UTC timezone-aware)."""
    venue: Venue
    """거래소 (KR/US)."""
    symbol: str
    """종목 코드."""
    price: float
    """현재가 (KRW for KR venue, USD for US venue)."""
    bid: float | None = None
    """매수호가."""
    ask: float | None = None
    """매도호가."""
    volume: int = 0
    """거래량 (주 단위)."""
    candle: Candle | None = None
    """OHLCV 캔들 데이터 (선택)."""
    features: Features | None = None
    """마켓 특성 지표 (선택)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "trace_id": "550e8400-e29b-41d4-a716-446655440000",
                "ts": "2026-03-01T09:30:00Z",
                "venue": "KR",
                "symbol": "005930",
                "price": 70000.0,
                "bid": 69900.0,
                "ask": 70100.0,
                "volume": 1000000,
                "candle": {
                    "open": 69500.0,
                    "high": 70500.0,
                    "low": 69000.0,
                    "close": 70000.0,
                    "volume": 1000000,
                },
                "features": {"volatility": 0.15, "trend": 0.3, "news_risk": 0.1},
            }
        }
    )


class TradeIdea(BaseModel):
    """분석 에이전트의 매매 아이디어."""

    trace_id: UUID = Field(default_factory=uuid4)
    """추적 ID (UUID v4)."""
    symbol: str
    """종목 코드."""
    side: Side
    """매매 방향 (BUY/SELL)."""
    confidence: float = Field(ge=0.0, le=1.0)
    """신뢰도 (0.0~1.0)."""
    horizon: Horizon = Horizon.SWING
    """매매 기간."""
    thesis: str = ""
    """투자 논리 (텍스트 격리: 로깅 전용, 의사결정 흐름에 영향 없음)."""
    entry: float
    """진입가 (KRW). KRX 호가 단위 보정은 Risk Gate/OMS 책임."""
    tp: float
    """익절가 (Take Profit, KRW)."""
    sl: float
    """손절가 (Stop Loss, KRW)."""
    constraints: dict[str, Any] | None = None  # type: ignore[misc]
    """추가 제약 조건 (선택)."""


class ApprovedOrderPlan(BaseModel):
    """리스크 게이트 승인 주문 계획."""

    trace_id: UUID
    """추적 ID (TradeIdea의 trace_id 상속)."""
    decision: Literal["APPROVED"] = "APPROVED"
    """결정 (항상 'APPROVED')."""
    mode: TradingMode
    """자동매매 모드 (PAUSED/PAPER/REAL)."""
    sizing: OrderSizing
    """주문 사이즈."""
    risk_checks: list[RiskCheckResult] = []
    """리스크 점검 결과 목록."""
    order: BrokerOrder
    """브로커 주문 정보."""
    capability_token: str | None = None
    """능력 토큰 (Phase 1b HMAC). Phase 1a에서는 None."""


class Rejected(BaseModel):
    """리스크 게이트 거부."""

    trace_id: UUID
    """추적 ID (TradeIdea의 trace_id 상속)."""
    decision: Literal["REJECTED"] = "REJECTED"
    """결정 (항상 'REJECTED')."""
    reason: str
    """거부 사유."""
    risk_checks: list[RiskCheckResult] = []
    """리스크 점검 결과 목록."""


class OrderResult(BaseModel):
    """OMS 주문 실행 결과."""

    trace_id: UUID
    """추적 ID (ApprovedOrderPlan의 trace_id 상속)."""
    broker_order_id: str
    """브로커 주문 ID."""
    idempotency_key: UUID = Field(default_factory=uuid4)
    """멱등성 키 (UUID v4). 중복 주문 방지."""
    status: OrderStatus
    """주문 상태."""
    fills: list[Fill] = []
    """체결 내역 목록."""
    fees: float = 0.0
    """총 수수료 (KRW)."""
    message: str | None = None
    """상태 메시지 (선택)."""


class ReconciliationResult(BaseModel):
    """재조정 결과. 브로커 포지션과 내부 상태 비교."""

    trace_id: UUID = Field(default_factory=uuid4)
    """추적 ID (UUID v4)."""
    timestamp: datetime
    """재조정 시각 (UTC timezone-aware)."""
    broker_positions: list[Position] = []
    """브로커 포지션 목록."""
    broker_cash: float = 0.0
    """브로커 현금 (KRW)."""
    broker_open_orders: int = 0
    """브로커 미체결 주문 건수."""
    internal_positions: list[Position] = []
    """내부 포지션 목록."""
    internal_cash: float = 0.0
    """내부 현금 (KRW)."""
    mismatches: list[Mismatch] = []
    """불일치 항목 목록."""
    resolution: str = ""
    """재조정 결과 설명."""


# ============================================================================
# ADDITIONAL MODELS
# ============================================================================


class Portfolio(BaseModel):
    """포트폴리오 상태."""

    positions: list[Position] = []
    """포지션 목록."""
    cash: float = 0.0
    """현금 (KRW)."""
    total_value: float = 0.0
    """총 자산 (KRW)."""
    daily_pnl: float = 0.0
    """일일 손익 (KRW)."""
    mdd: float = 0.0
    """최대 낙폭 (%)."""
    updated_at: datetime
    """마지막 업데이트 시각 (UTC timezone-aware)."""


class RiskPolicy(BaseModel):
    """리스크 정책. Risk Gate에서 사용."""

    profile_name: str = "defensive"
    """정책 프로필명 (conservative/defensive/aggressive)."""
    max_position_pct: float = 5.0
    """단일 종목 최대 비중 (%)."""
    max_positions: int = 5
    """동시 보유 최대 종목 수."""
    max_drawdown_pct: float = 7.0
    """최대 낙폭 한도 (%)."""
    daily_loss_limit_pct: float = 1.5
    """일일 손실 한도 (%)."""
    max_daily_orders: int = 5
    """일일 최대 주문 건수."""
    market_open_delay_minutes: int = 60
    """장 시작 후 거래 지연 시간 (분). 10:00 AM KST = 장 시작 후 60분."""
    data_collection_start: str = "08:30"
    """데이터 수집 시작 시각 (KST, HH:MM)."""
    data_collection_end: str = "15:00"
    """데이터 수집 종료 시각 (KST, HH:MM)."""
    trading_start: str = "10:00"
    """거래 시작 시각 (KST, HH:MM)."""
    trading_end: str = "15:00"
    """거래 종료 시각 (KST, HH:MM)."""
