"""
tests/test_risk_gate.py - RiskGate 종합 테스트 (25+ cases).

8개 규칙 + 사이징 + 승인 로직 검증.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from engine.risk_gate import RiskGate
from schemas.models import (
    ApprovedOrderPlan,
    Horizon,
    Portfolio,
    Position,
    Rejected,
    RiskPolicy,
    Side,
    TradingMode,
    TradeIdea,
)

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def gate() -> RiskGate:
    """RiskGate 인스턴스."""
    return RiskGate()


@pytest.fixture
def policy() -> RiskPolicy:
    """기본 리스크 정책."""
    return RiskPolicy()


@pytest.fixture
def now_trading_hours() -> datetime:
    """KST 11:00 = UTC 02:00 (거래시간 내)."""
    return datetime(2026, 3, 2, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def data_asof(now_trading_hours: datetime) -> datetime:
    """10분 전 (신선한 데이터)."""
    return now_trading_hours - timedelta(minutes=10)


@pytest.fixture
def portfolio() -> Portfolio:
    """기본 포트폴리오 (현금 1000만원, 포지션 없음)."""
    return Portfolio(
        positions=[],
        cash=10_000_000,
        total_value=10_000_000,
        daily_pnl=0.0,
        mdd=0.0,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def buy_idea() -> TradeIdea:
    """BUY 아이디어."""
    return TradeIdea(
        symbol="005930",
        side=Side.BUY,
        confidence=0.8,
        horizon=Horizon.SWING,
        entry=70000.0,
        tp=75000.0,
        sl=65000.0,
    )


@pytest.fixture
def sell_idea() -> TradeIdea:
    """SELL 아이디어."""
    return TradeIdea(
        symbol="005930",
        side=Side.SELL,
        confidence=0.8,
        horizon=Horizon.SWING,
        entry=72000.0,
        tp=0.0,
        sl=0.0,
    )


# ============================================================================
# TestTradingMode (2 cases)
# ============================================================================


class TestTradingMode:
    """거래 모드 규칙 테스트."""

    def test_paused_rejects_buy(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """PAUSED 모드에서 BUY 거부."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAUSED,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "PAUSED" in result.reason

    def test_paused_rejects_sell(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """PAUSED 모드에서 SELL도 거부."""
        portfolio.positions = [
            Position(
                symbol="005930",
                qty=100,
                avg_price=70000.0,
                current_price=72000.0,
            )
        ]
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAUSED,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "PAUSED" in result.reason

    def test_paper_allows_buy(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """PAPER 모드에서 BUY 허용 (다른 규칙 통과 시)."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_real_allows_buy(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """REAL 모드에서 BUY 허용 (다른 규칙 통과 시)."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.REAL,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)


# ============================================================================
# TestMarketHours (5 cases)
# ============================================================================


class TestMarketHours:
    """거래시간 규칙 테스트."""

    def test_within_trading_hours_approved(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """KST 11:00 (거래시간 내) → 승인."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_before_trading_start_rejected(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        data_asof: datetime,
    ) -> None:
        """KST 09:30 (거래시간 전) → 거부."""
        # KST 09:30 = UTC 00:30
        now = datetime(2026, 3, 2, 0, 30, tzinfo=timezone.utc)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "거래시간 외" in result.reason

    def test_after_trading_end_rejected(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        data_asof: datetime,
    ) -> None:
        """KST 15:30 (거래시간 후) → 거부."""
        # KST 15:30 = UTC 06:30
        now = datetime(2026, 3, 2, 6, 30, tzinfo=timezone.utc)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "거래시간 외" in result.reason

    def test_exactly_at_trading_start(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
    ) -> None:
        """KST 10:00 (거래시간 시작, inclusive) → 승인."""
        # KST 10:00 = UTC 01:00
        now = datetime(2026, 3, 2, 1, 0, tzinfo=timezone.utc)
        data_asof = now - timedelta(minutes=10)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_exactly_at_trading_end(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
    ) -> None:
        """KST 15:00 (거래시간 종료, exclusive) → 거부."""
        # KST 15:00 = UTC 06:00
        now = datetime(2026, 3, 2, 6, 0, tzinfo=timezone.utc)
        data_asof = now - timedelta(minutes=10)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "거래시간 외" in result.reason


# ============================================================================
# TestDailyLoss (3 cases)
# ============================================================================


class TestDailyLoss:
    """일일 손실 규칙 테스트 (BUY 전용)."""

    def test_daily_loss_within_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """일일 손실 1% < 한도 1.5% → 승인."""
        portfolio = Portfolio(
            positions=[],
            cash=10_000_000,
            total_value=10_000_000,
            daily_pnl=-100_000,  # 1% 손실
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_daily_loss_exceeds_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """일일 손실 2% ≥ 한도 1.5% → 거부."""
        portfolio = Portfolio(
            positions=[],
            cash=10_000_000,
            total_value=10_000_000,
            daily_pnl=-200_000,  # 2% 손실
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "일일 손실" in result.reason

    def test_daily_loss_allows_sell(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """SELL은 일일 손실 규칙 skip → 승인."""
        portfolio = Portfolio(
            positions=[
                Position(
                    symbol="005930",
                    qty=100,
                    avg_price=70000.0,
                    current_price=72000.0,
                )
            ],
            cash=10_000_000,
            total_value=17_200_000,
            daily_pnl=-200_000,  # 2% 손실 (한도 초과)
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)


# ============================================================================
# TestMDD (3 cases)
# ============================================================================


class TestMDD:
    """MDD 규칙 테스트 (BUY 전용)."""

    def test_mdd_within_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """MDD 5% < 한도 7% → 승인."""
        portfolio = Portfolio(
            positions=[],
            cash=10_000_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=5.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_mdd_exceeds_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """MDD 8% ≥ 한도 7% → 거부."""
        portfolio = Portfolio(
            positions=[],
            cash=10_000_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=8.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "MDD" in result.reason

    def test_mdd_allows_sell(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """SELL은 MDD 규칙 skip → 승인."""
        portfolio = Portfolio(
            positions=[
                Position(
                    symbol="005930",
                    qty=100,
                    avg_price=70000.0,
                    current_price=72000.0,
                )
            ],
            cash=10_000_000,
            total_value=17_200_000,
            daily_pnl=0.0,
            mdd=8.0,  # 한도 초과
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)


# ============================================================================
# TestConcentration (3 cases)
# ============================================================================


class TestConcentration:
    """종목 집중도 규칙 테스트 (BUY 전용)."""

    def test_no_existing_position_passes(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """신규 종목 BUY, 기존 포지션 없음 → 승인."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_existing_position_at_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """기존 포지션 비중 5% ≥ 한도 5% → 거부."""
        portfolio = Portfolio(
            positions=[
                Position(
                    symbol="005930",
                    qty=714,  # 714 * 70000 = 49,980,000 ≈ 5% of 10M
                    avg_price=70000.0,
                    current_price=70000.0,
                )
            ],
            cash=5_020_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "비중" in result.reason

    def test_sell_skips_concentration(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """SELL은 집중도 규칙 skip → 승인."""
        portfolio = Portfolio(
            positions=[
                Position(
                    symbol="005930",
                    qty=714,
                    avg_price=70000.0,
                    current_price=70000.0,
                )
            ],
            cash=5_020_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)


# ============================================================================
# TestMaxPositions (3 cases)
# ============================================================================


class TestMaxPositions:
    """최대 보유 종목 수 규칙 테스트 (BUY 전용)."""

    def test_within_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """3개 종목 보유, 한도 5개 → 신규 종목 BUY 승인."""
        portfolio = Portfolio(
            positions=[
                Position(symbol="000001", qty=100, avg_price=10000.0),
                Position(symbol="000002", qty=100, avg_price=10000.0),
                Position(symbol="000003", qty=100, avg_price=10000.0),
            ],
            cash=9_700_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_at_limit_new_symbol(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """5개 종목 보유, 한도 5개, 신규 종목 BUY → 거부."""
        portfolio = Portfolio(
            positions=[
                Position(symbol="000001", qty=100, avg_price=10000.0),
                Position(symbol="000002", qty=100, avg_price=10000.0),
                Position(symbol="000003", qty=100, avg_price=10000.0),
                Position(symbol="000004", qty=100, avg_price=10000.0),
                Position(symbol="000005", qty=100, avg_price=10000.0),
            ],
            cash=9_500_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "보유 종목 수" in result.reason

    def test_at_limit_existing_symbol(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """5개 종목 보유, 한도 5개, 기존 종목 추가 BUY → 승인."""
        portfolio = Portfolio(
            positions=[
                Position(symbol="005930", qty=3, avg_price=70000.0),  # 210K = 2.1%
                Position(symbol="000002", qty=100, avg_price=10000.0),
                Position(symbol="000003", qty=100, avg_price=10000.0),
                Position(symbol="000004", qty=100, avg_price=10000.0),
                Position(symbol="000005", qty=100, avg_price=10000.0),
            ],
            cash=8_200_000,
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)


# ============================================================================
# TestDailyOrders (3 cases)
# ============================================================================


class TestDailyOrders:
    """일일 주문 건수 규칙 테스트."""

    def test_within_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """3개 주문, 한도 5개 → 승인."""
        gate.set_daily_order_count(3)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_at_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """5개 주문, 한도 5개 → 거부."""
        gate.set_daily_order_count(5)
        gate.set_last_reset_date(now_trading_hours.astimezone(KST).date())
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "일일 주문" in result.reason

    def test_auto_reset_new_day(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        data_asof: datetime,
    ) -> None:
        """어제 5개 주문 → 오늘 자동 리셋 → 승인."""
        yesterday = datetime(2026, 3, 1, 2, 0, tzinfo=timezone.utc)
        gate.set_daily_order_count(5)
        # Manually set last_reset_date to simulate yesterday's count
        gate.set_last_reset_date(yesterday.astimezone(KST).date())

        today = datetime(2026, 3, 2, 2, 0, tzinfo=timezone.utc)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            today,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        assert gate.get_daily_order_count() == 1  # 리셋 후 1 증가


# ============================================================================
# TestDataStaleness (3 cases)
# ============================================================================


class TestDataStaleness:
    """데이터 신선도 규칙 테스트."""

    def test_fresh_data_passes(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """데이터 10분 전 (신선) → 승인."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_stale_data_rejected(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
    ) -> None:
        """데이터 60분 전 (stale, > 30분) → 거부."""
        stale_data = now_trading_hours - timedelta(minutes=60)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            stale_data,
        )
        assert isinstance(result, Rejected)
        assert "데이터 나이" in result.reason

    def test_none_data_asof_rejected(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
    ) -> None:
        """data_asof=None → 거부 (fail closed)."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            None,
        )
        assert isinstance(result, Rejected)
        assert "데이터 기준 시각 미제공" in result.reason


# ============================================================================
# TestSizing (3 cases)
# ============================================================================


class TestSizing:
    """주문 사이징 규칙 테스트."""

    def test_buy_sizing_respects_limit(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """BUY 사이징: total=10M, max_pct=5% → max notional 500K."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        # 500K / 70K = 7.14... → qty = 7
        assert result.sizing.qty == 7
        assert result.sizing.notional == 7 * 70000.0

    def test_buy_sizing_zero_rejects(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """BUY 사이징 결과 qty=0 (현금 부족) → 거부."""
        portfolio = Portfolio(
            positions=[],
            cash=0,  # 현금 없음
            total_value=10_000_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "주문 수량 계산 결과 0" in result.reason

    def test_sell_sizing_full_position(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """SELL 사이징: 보유 전량 매도."""
        portfolio = Portfolio(
            positions=[
                Position(
                    symbol="005930",
                    qty=100,
                    avg_price=70000.0,
                    current_price=72000.0,
                )
            ],
            cash=10_000_000,
            total_value=17_200_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        assert result.sizing.qty == 100


# ============================================================================
# TestApproval (3 cases)
# ============================================================================


class TestApproval:
    """승인 로직 테스트."""

    def test_full_approval_returns_plan(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """모든 규칙 통과 → ApprovedOrderPlan 반환."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        assert result.trace_id == buy_idea.trace_id
        assert result.mode == TradingMode.PAPER
        assert result.sizing.qty > 0
        assert result.order.symbol == "005930"
        assert result.order.side == Side.BUY

    def test_approval_increments_daily_count(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """승인 시 일일 주문 카운트 증가."""
        initial_count = gate.get_daily_order_count()
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        assert gate.get_daily_order_count() == initial_count + 1

    def test_all_checks_in_result(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """결과에 모든 점검 결과 포함."""
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        # BUY: 4개 공통 + 4개 BUY 전용 = 8개
        assert len(result.risk_checks) == 8
        rule_names = {c.rule_name for c in result.risk_checks}
        expected = {
            "trading_mode",
            "market_hours",
            "daily_orders",
            "data_staleness",
            "daily_loss",
            "mdd",
            "concentration",
            "max_positions",
        }
        assert rule_names == expected


# ============================================================================
# TestSellSpecific (2 cases)
# ============================================================================


class TestSellSpecific:
    """SELL 전용 규칙 테스트."""

    def test_sell_has_position_passes(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """SELL: 보유 종목 → 승인."""
        portfolio = Portfolio(
            positions=[
                Position(
                    symbol="005930",
                    qty=100,
                    avg_price=70000.0,
                    current_price=72000.0,
                )
            ],
            cash=10_000_000,
            total_value=17_200_000,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)

    def test_sell_no_position_rejected(
        self,
        gate: RiskGate,
        sell_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """SELL: 미보유 종목 → 거부."""
        result = gate.evaluate(
            sell_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "미보유 종목" in result.reason


# ============================================================================
# TestEdgeCases (3 cases)
# ============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_zero_total_value_rejects(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """total_value=0 → 거부 (daily_loss 체크에서)."""
        portfolio = Portfolio(
            positions=[],
            cash=0,
            total_value=0,
            daily_pnl=0.0,
            mdd=0.0,
            updated_at=datetime.now(timezone.utc),
        )
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)

    def test_negative_entry_price_zero_qty(
        self,
        gate: RiskGate,
        policy: RiskPolicy,
        portfolio: Portfolio,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        """entry=0 → qty=0 → 거부."""
        idea = TradeIdea(
            symbol="005930",
            side=Side.BUY,
            confidence=0.8,
            horizon=Horizon.SWING,
            entry=0.0,  # 0 진입가
            tp=75000.0,
            sl=65000.0,
        )
        result = gate.evaluate(
            idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, Rejected)
        assert "주문 수량 계산 결과 0" in result.reason

    def test_multiple_failures_first_reason_returned(
        self,
        gate: RiskGate,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
    ) -> None:
        """여러 규칙 실패 → 첫 번째 실패 사유 반환."""
        # PAUSED + 거래시간 외 동시 실패
        now = datetime(2026, 3, 2, 0, 30, tzinfo=timezone.utc)  # 거래시간 외
        data_asof = now - timedelta(minutes=10)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAUSED,  # 모드 실패
            now,
            data_asof,
        )
        assert isinstance(result, Rejected)
        # 첫 번째 실패는 trading_mode
        assert "PAUSED" in result.reason
