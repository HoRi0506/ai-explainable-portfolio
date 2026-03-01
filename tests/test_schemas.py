"""
tests/test_schemas.py - Pydantic v2 스키마 테스트.

테스트 범위:
- 각 모델의 유효한 인스턴스 생성
- dict 직렬화 (model_dump)
- JSON 직렬화 (model_dump_json)
- JSON 역직렬화 (model_validate_json)
- 라운드트립 동등성 검증
- 유효하지 않은 데이터에 대한 ValidationError 발생
- UUID 생성 (default_factory)
- Enum 직렬화/역직렬화
- Optional 필드 (None 값)
- 중첩 모델 라운드트립
"""


from datetime import datetime, timezone
from typing import cast
from uuid import UUID
from uuid import UUID

import pytest
from pydantic import ValidationError

from schemas import (
    Alert,
    AlertAction,
    AlertSeverity,
    ApprovedOrderPlan,
    BrokerOrder,
    Candle,
    ConfigChangeEvent,
    Features,
    Fill,
    Horizon,
    MarketSnapshot,
    Mismatch,
    OrderResult,
    OrderSizing,
    OrderStatus,
    Position,
    Portfolio,
    ReconciliationResult,
    Rejected,
    RiskCheckResult,
    RiskPolicy,
    Side,
    TradeIdea,
    TradingMode,
    Venue,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def utc_now():
    """현재 UTC 시각."""
    return datetime.now(timezone.utc)


@pytest.fixture
def sample_candle():
    """샘플 캔들 데이터."""
    return Candle(
        open=69500.0,
        high=70500.0,
        low=69000.0,
        close=70000.0,
        volume=1000000,
    )


@pytest.fixture
def sample_features():
    """샘플 마켓 특성."""
    return Features(volatility=0.15, trend=0.3, news_risk=0.1)


@pytest.fixture
def sample_position():
    """샘플 포지션."""
    return Position(
        symbol="005930",
        qty=100,
        avg_price=70000.0,
        current_price=70500.0,
        unrealized_pnl=50000.0,
        realized_pnl=0.0,
    )


@pytest.fixture
def sample_fill():
    """샘플 체결."""
    return Fill(
        fill_id="FILL001",
        qty=100,
        price=70000.0,
        timestamp=datetime.now(timezone.utc),
        fee=1000.0,
    )


@pytest.fixture
def sample_order_sizing():
    """샘플 주문 사이즈."""
    return OrderSizing(qty=100, notional=7000000.0, weight_pct=5.0)


@pytest.fixture
def sample_risk_check_result():
    """샘플 리스크 점검 결과."""
    return RiskCheckResult(
        rule_name="max_position_pct",
        passed=True,
        detail="Position 5.0% <= max 5.0%",
    )


@pytest.fixture
def sample_mismatch():
    """샘플 불일치."""
    return Mismatch(
        field="qty",
        broker_value="100",
        internal_value="95",
        severity=AlertSeverity.MEDIUM,
    )


@pytest.fixture
def sample_broker_order():
    """샘플 브로커 주문."""
    return BrokerOrder(
        symbol="005930",
        side=Side.BUY,
        qty=100,
        price=70000.0,
        order_type="LIMIT",
        time_in_force="GTC",
    )


# ============================================================================
# ENUM TESTS
# ============================================================================


class TestEnums:
    """Enum 직렬화/역직렬화 테스트."""

    def test_venue_enum(self):
        """Venue enum 테스트."""
        assert Venue.KR.value == "KR"
        assert Venue.US.value == "US"

    def test_side_enum(self):
        """Side enum 테스트."""
        assert Side.BUY.value == "BUY"
        assert Side.SELL.value == "SELL"

    def test_trading_mode_enum(self):
        """TradingMode enum 테스트."""
        assert TradingMode.PAUSED.value == "PAUSED"
        assert TradingMode.PAPER.value == "PAPER"
        assert TradingMode.REAL.value == "REAL"

    def test_order_status_enum(self):
        """OrderStatus enum 테스트."""
        assert OrderStatus.NEW.value == "NEW"
        assert OrderStatus.SUBMITTED.value == "SUBMITTED"
        assert OrderStatus.PARTIAL.value == "PARTIAL"
        assert OrderStatus.FILLED.value == "FILLED"
        assert OrderStatus.CANCELED.value == "CANCELED"
        assert OrderStatus.REJECTED.value == "REJECTED"

    def test_horizon_enum(self):
        """Horizon enum 테스트."""
        assert Horizon.INTRADAY.value == "INTRADAY"
        assert Horizon.SWING.value == "SWING"
        assert Horizon.POSITION.value == "POSITION"

    def test_alert_severity_enum(self):
        """AlertSeverity enum 테스트."""
        assert AlertSeverity.LOW.value == "LOW"
        assert AlertSeverity.MEDIUM.value == "MEDIUM"
        assert AlertSeverity.HIGH.value == "HIGH"
        assert AlertSeverity.CRITICAL.value == "CRITICAL"

    def test_alert_action_enum(self):
        """AlertAction enum 테스트."""
        assert AlertAction.HOLD.value == "HOLD"
        assert AlertAction.REDUCE.value == "REDUCE"
        assert AlertAction.STOP.value == "STOP"


# ============================================================================
# NESTED MODEL TESTS
# ============================================================================


class TestCandle:
    """Candle 모델 테스트."""

    def test_candle_creation(self, sample_candle):
        """Candle 생성 테스트."""
        assert sample_candle.open == 69500.0
        assert sample_candle.high == 70500.0
        assert sample_candle.low == 69000.0
        assert sample_candle.close == 70000.0
        assert sample_candle.volume == 1000000

    def test_candle_roundtrip(self, sample_candle):
        """Candle 라운드트립 테스트."""
        data = sample_candle.model_dump()
        json_str = sample_candle.model_dump_json()
        restored = Candle.model_validate_json(json_str)
        assert restored == sample_candle


class TestFeatures:
    """Features 모델 테스트."""

    def test_features_with_all_fields(self, sample_features):
        """모든 필드가 있는 Features 테스트."""
        assert sample_features.volatility == 0.15
        assert sample_features.trend == 0.3
        assert sample_features.news_risk == 0.1

    def test_features_with_none_values(self):
        """None 값을 가진 Features 테스트."""
        features = Features()
        assert features.volatility is None
        assert features.trend is None
        assert features.news_risk is None

    def test_features_roundtrip(self, sample_features):
        """Features 라운드트립 테스트."""
        json_str = sample_features.model_dump_json()
        restored = Features.model_validate_json(json_str)
        assert restored == sample_features


class TestPosition:
    """Position 모델 테스트."""

    def test_position_creation(self, sample_position):
        """Position 생성 테스트."""
        assert sample_position.symbol == "005930"
        assert sample_position.qty == 100
        assert sample_position.avg_price == 70000.0

    def test_position_qty_must_be_integer(self):
        """Position qty는 정수여야 함."""
        with pytest.raises(ValidationError):
            Position(
                symbol="005930",
                qty=cast(int, 100.5),  # 정수 아님
                avg_price=70000.0,
            )

    def test_position_roundtrip(self, sample_position):
        """Position 라운드트립 테스트."""
        json_str = sample_position.model_dump_json()
        restored = Position.model_validate_json(json_str)
        assert restored == sample_position


class TestFill:
    """Fill 모델 테스트."""

    def test_fill_creation(self, sample_fill):
        """Fill 생성 테스트."""
        assert sample_fill.fill_id == "FILL001"
        assert sample_fill.qty == 100
        assert sample_fill.price == 70000.0
        assert sample_fill.fee == 1000.0

    def test_fill_timestamp_is_utc(self, sample_fill):
        """Fill timestamp는 UTC timezone-aware."""
        assert sample_fill.timestamp.tzinfo is not None

    def test_fill_roundtrip(self, sample_fill):
        """Fill 라운드트립 테스트."""
        json_str = sample_fill.model_dump_json()
        restored = Fill.model_validate_json(json_str)
        assert restored.fill_id == sample_fill.fill_id
        assert restored.qty == sample_fill.qty


class TestOrderSizing:
    """OrderSizing 모델 테스트."""

    def test_order_sizing_creation(self, sample_order_sizing):
        """OrderSizing 생성 테스트."""
        assert sample_order_sizing.qty == 100
        assert sample_order_sizing.notional == 7000000.0
        assert sample_order_sizing.weight_pct == 5.0

    def test_order_sizing_roundtrip(self, sample_order_sizing):
        """OrderSizing 라운드트립 테스트."""
        json_str = sample_order_sizing.model_dump_json()
        restored = OrderSizing.model_validate_json(json_str)
        assert restored == sample_order_sizing


class TestRiskCheckResult:
    """RiskCheckResult 모델 테스트."""

    def test_risk_check_result_creation(self, sample_risk_check_result):
        """RiskCheckResult 생성 테스트."""
        assert sample_risk_check_result.rule_name == "max_position_pct"
        assert sample_risk_check_result.passed is True

    def test_risk_check_result_roundtrip(self, sample_risk_check_result):
        """RiskCheckResult 라운드트립 테스트."""
        json_str = sample_risk_check_result.model_dump_json()
        restored = RiskCheckResult.model_validate_json(json_str)
        assert restored == sample_risk_check_result


class TestMismatch:
    """Mismatch 모델 테스트."""

    def test_mismatch_creation(self, sample_mismatch):
        """Mismatch 생성 테스트."""
        assert sample_mismatch.field == "qty"
        assert sample_mismatch.severity == AlertSeverity.MEDIUM

    def test_mismatch_roundtrip(self, sample_mismatch):
        """Mismatch 라운드트립 테스트."""
        json_str = sample_mismatch.model_dump_json()
        restored = Mismatch.model_validate_json(json_str)
        assert restored == sample_mismatch


class TestBrokerOrder:
    """BrokerOrder 모델 테스트."""

    def test_broker_order_creation(self, sample_broker_order):
        """BrokerOrder 생성 테스트."""
        assert sample_broker_order.symbol == "005930"
        assert sample_broker_order.side == Side.BUY
        assert sample_broker_order.qty == 100

    def test_broker_order_market_order(self):
        """시장가 주문 (price=None)."""
        order = BrokerOrder(
            symbol="005930",
            side=Side.BUY,
            qty=100,
            price=None,
            order_type="MARKET",
        )
        assert order.price is None

    def test_broker_order_roundtrip(self, sample_broker_order):
        """BrokerOrder 라운드트립 테스트."""
        json_str = sample_broker_order.model_dump_json()
        restored = BrokerOrder.model_validate_json(json_str)
        assert restored == sample_broker_order


# ============================================================================
# MAIN MODEL TESTS (6)
# ============================================================================


class TestMarketSnapshot:
    """MarketSnapshot 모델 테스트."""

    def test_market_snapshot_creation(self, utc_now, sample_candle, sample_features):
        """MarketSnapshot 생성 테스트."""
        snapshot = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
            bid=69900.0,
            ask=70100.0,
            volume=1000000,
            candle=sample_candle,
            features=sample_features,
        )
        assert snapshot.symbol == "005930"
        assert snapshot.venue == Venue.KR
        assert snapshot.price == 70000.0
        assert snapshot.trace_id is not None

    def test_market_snapshot_uuid_generation(self, utc_now):
        """MarketSnapshot UUID 생성 테스트."""
        snapshot1 = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
        )
        snapshot2 = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
        )
        assert snapshot1.trace_id != snapshot2.trace_id

    def test_market_snapshot_nested_roundtrip(self, utc_now, sample_candle):
        """MarketSnapshot 중첩 모델 라운드트립."""
        snapshot = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
            candle=sample_candle,
        )
        json_str = snapshot.model_dump_json()
        restored = MarketSnapshot.model_validate_json(json_str)
        assert restored.candle == sample_candle

    def test_market_snapshot_roundtrip(self, utc_now, sample_candle, sample_features):
        """MarketSnapshot 라운드트립 테스트."""
        snapshot = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
            bid=69900.0,
            ask=70100.0,
            volume=1000000,
            candle=sample_candle,
            features=sample_features,
        )
        json_str = snapshot.model_dump_json()
        restored = MarketSnapshot.model_validate_json(json_str)
        assert restored.symbol == snapshot.symbol
        assert restored.price == snapshot.price


class TestTradeIdea:
    """TradeIdea 모델 테스트."""

    def test_trade_idea_creation(self):
        """TradeIdea 생성 테스트."""
        idea = TradeIdea(
            symbol="005930",
            side=Side.BUY,
            confidence=0.75,
            horizon=Horizon.SWING,
            thesis="Strong uptrend with support at 69000",
            entry=70000.0,
            tp=75000.0,
            sl=68000.0,
        )
        assert idea.symbol == "005930"
        assert idea.confidence == 0.75
        assert idea.trace_id is not None

    def test_trade_idea_confidence_validation(self):
        """TradeIdea confidence 범위 검증."""
        # confidence > 1.0 should fail
        with pytest.raises(ValidationError):
            TradeIdea(
                symbol="005930",
                side=Side.BUY,
                confidence=1.5,  # > 1.0
                entry=70000.0,
                tp=75000.0,
                sl=68000.0,
            )

        # confidence < 0.0 should fail
        with pytest.raises(ValidationError):
            TradeIdea(
                symbol="005930",
                side=Side.BUY,
                confidence=-0.1,  # < 0.0
                entry=70000.0,
                tp=75000.0,
                sl=68000.0,
            )

    def test_trade_idea_roundtrip(self):
        """TradeIdea 라운드트립 테스트."""
        idea = TradeIdea(
            symbol="005930",
            side=Side.BUY,
            confidence=0.75,
            horizon=Horizon.SWING,
            thesis="Strong uptrend",
            entry=70000.0,
            tp=75000.0,
            sl=68000.0,
        )
        json_str = idea.model_dump_json()
        restored = TradeIdea.model_validate_json(json_str)
        assert restored.symbol == idea.symbol
        assert restored.confidence == idea.confidence


class TestApprovedOrderPlan:
    """ApprovedOrderPlan 모델 테스트."""

    def test_approved_order_plan_creation(
        self, sample_order_sizing, sample_broker_order, sample_risk_check_result
    ):
        """ApprovedOrderPlan 생성 테스트."""
        plan = ApprovedOrderPlan(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            decision="APPROVED",
            mode=TradingMode.PAPER,
            sizing=sample_order_sizing,
            risk_checks=[sample_risk_check_result],
            order=sample_broker_order,
        )
        assert plan.decision == "APPROVED"
        assert plan.mode == TradingMode.PAPER
        assert len(plan.risk_checks) == 1

    def test_approved_order_plan_roundtrip(
        self, sample_order_sizing, sample_broker_order
    ):
        """ApprovedOrderPlan 라운드트립 테스트."""
        plan = ApprovedOrderPlan(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            decision="APPROVED",
            mode=TradingMode.PAPER,
            sizing=sample_order_sizing,
            order=sample_broker_order,
        )
        json_str = plan.model_dump_json()
        restored = ApprovedOrderPlan.model_validate_json(json_str)
        assert restored.decision == plan.decision
        assert restored.mode == plan.mode


class TestRejected:
    """Rejected 모델 테스트."""

    def test_rejected_creation(self, sample_risk_check_result):
        """Rejected 생성 테스트."""
        rejected = Rejected(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            decision="REJECTED",
            reason="Position size exceeds max_position_pct",
            risk_checks=[sample_risk_check_result],
        )
        assert rejected.decision == "REJECTED"
        assert "exceeds" in rejected.reason

    def test_rejected_roundtrip(self):
        """Rejected 라운드트립 테스트."""
        rejected = Rejected(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            decision="REJECTED",
            reason="Test reason",
        )
        json_str = rejected.model_dump_json()
        restored = Rejected.model_validate_json(json_str)
        assert restored.decision == rejected.decision
        assert restored.reason == rejected.reason


class TestOrderResult:
    """OrderResult 모델 테스트."""

    def test_order_result_creation(self, sample_fill):
        """OrderResult 생성 테스트."""
        result = OrderResult(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            broker_order_id="ORD001",
            status=OrderStatus.FILLED,
            fills=[sample_fill],
            fees=1000.0,
        )
        assert result.broker_order_id == "ORD001"
        assert result.status == OrderStatus.FILLED
        assert len(result.fills) == 1

    def test_order_result_idempotency_key_generation(self):
        """OrderResult idempotency_key 생성 테스트."""
        result1 = OrderResult(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            broker_order_id="ORD001",
            status=OrderStatus.NEW,
        )
        result2 = OrderResult(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            broker_order_id="ORD001",
            status=OrderStatus.NEW,
        )
        assert result1.idempotency_key != result2.idempotency_key

    def test_order_result_nested_roundtrip(self, sample_fill):
        """OrderResult 중첩 모델 라운드트립."""
        result = OrderResult(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            broker_order_id="ORD001",
            status=OrderStatus.FILLED,
            fills=[sample_fill],
        )
        json_str = result.model_dump_json()
        restored = OrderResult.model_validate_json(json_str)
        assert len(restored.fills) == 1
        assert restored.fills[0].fill_id == sample_fill.fill_id

    def test_order_result_roundtrip(self, sample_fill):
        """OrderResult 라운드트립 테스트."""
        result = OrderResult(
            trace_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
            broker_order_id="ORD001",
            status=OrderStatus.FILLED,
            fills=[sample_fill],
            fees=1000.0,
        )
        json_str = result.model_dump_json()
        restored = OrderResult.model_validate_json(json_str)
        assert restored.broker_order_id == result.broker_order_id
        assert restored.status == result.status


class TestReconciliationResult:
    """ReconciliationResult 모델 테스트."""

    def test_reconciliation_result_creation(
        self, utc_now, sample_position, sample_mismatch
    ):
        """ReconciliationResult 생성 테스트."""
        result = ReconciliationResult(
            timestamp=utc_now,
            broker_positions=[sample_position],
            broker_cash=1000000.0,
            internal_positions=[sample_position],
            internal_cash=1000000.0,
            mismatches=[sample_mismatch],
        )
        assert len(result.broker_positions) == 1
        assert len(result.mismatches) == 1

    def test_reconciliation_result_roundtrip(self, utc_now, sample_position):
        """ReconciliationResult 라운드트립 테스트."""
        result = ReconciliationResult(
            timestamp=utc_now,
            broker_positions=[sample_position],
            broker_cash=1000000.0,
            internal_positions=[sample_position],
            internal_cash=1000000.0,
        )
        json_str = result.model_dump_json()
        restored = ReconciliationResult.model_validate_json(json_str)
        assert len(restored.broker_positions) == 1


# ============================================================================
# ADDITIONAL MODEL TESTS
# ============================================================================


class TestPortfolio:
    """Portfolio 모델 테스트."""

    def test_portfolio_creation(self, utc_now, sample_position):
        """Portfolio 생성 테스트."""
        portfolio = Portfolio(
            positions=[sample_position],
            cash=1000000.0,
            total_value=8000000.0,
            daily_pnl=50000.0,
            mdd=2.5,
            updated_at=utc_now,
        )
        assert len(portfolio.positions) == 1
        assert portfolio.cash == 1000000.0

    def test_portfolio_roundtrip(self, utc_now, sample_position):
        """Portfolio 라운드트립 테스트."""
        portfolio = Portfolio(
            positions=[sample_position],
            cash=1000000.0,
            total_value=8000000.0,
            daily_pnl=50000.0,
            mdd=2.5,
            updated_at=utc_now,
        )
        json_str = portfolio.model_dump_json()
        restored = Portfolio.model_validate_json(json_str)
        assert restored.cash == portfolio.cash


class TestRiskPolicy:
    """RiskPolicy 모델 테스트."""

    def test_risk_policy_creation(self):
        """RiskPolicy 생성 테스트."""
        policy = RiskPolicy(
            profile_name="defensive",
            max_position_pct=5.0,
            max_positions=5,
            max_drawdown_pct=7.0,
            daily_loss_limit_pct=1.5,
            max_daily_orders=5,
        )
        assert policy.profile_name == "defensive"
        assert policy.max_position_pct == 5.0

    def test_risk_policy_defaults(self):
        """RiskPolicy 기본값 테스트."""
        policy = RiskPolicy()
        assert policy.profile_name == "defensive"
        assert policy.trading_start == "10:00"
        assert policy.trading_end == "15:00"

    def test_risk_policy_roundtrip(self):
        """RiskPolicy 라운드트립 테스트."""
        policy = RiskPolicy(
            profile_name="aggressive",
            max_position_pct=10.0,
            max_positions=8,
        )
        json_str = policy.model_dump_json()
        restored = RiskPolicy.model_validate_json(json_str)
        assert restored.profile_name == policy.profile_name
        assert restored.max_position_pct == policy.max_position_pct


# ============================================================================
# EVENT TESTS
# ============================================================================


class TestAlert:
    """Alert 모델 테스트."""

    def test_alert_creation(self, utc_now):
        """Alert 생성 테스트."""
        alert = Alert(
            ts=utc_now,
            severity=AlertSeverity.HIGH,
            message="Position loss exceeds 5%",
            action=AlertAction.REDUCE,
        )
        assert alert.severity == AlertSeverity.HIGH
        assert alert.action == AlertAction.REDUCE
        assert alert.trace_id is not None

    def test_alert_uuid_generation(self, utc_now):
        """Alert UUID 생성 테스트."""
        alert1 = Alert(
            ts=utc_now,
            severity=AlertSeverity.MEDIUM,
            message="Test",
            action=AlertAction.HOLD,
        )
        alert2 = Alert(
            ts=utc_now,
            severity=AlertSeverity.MEDIUM,
            message="Test",
            action=AlertAction.HOLD,
        )
        assert alert1.trace_id != alert2.trace_id

    def test_alert_roundtrip(self, utc_now):
        """Alert 라운드트립 테스트."""
        alert = Alert(
            ts=utc_now,
            severity=AlertSeverity.CRITICAL,
            message="Kill switch triggered",
            action=AlertAction.STOP,
        )
        json_str = alert.model_dump_json()
        restored = Alert.model_validate_json(json_str)
        assert restored.severity == alert.severity
        assert restored.action == alert.action


class TestConfigChangeEvent:
    """ConfigChangeEvent 모델 테스트."""

    def test_config_change_event_creation(self, utc_now):
        """ConfigChangeEvent 생성 테스트."""
        event = ConfigChangeEvent(
            timestamp=utc_now,
            changed_by="admin",
            old_value=5.0,
            new_value=7.0,
            version_tag="v1.0.1",
        )
        assert event.changed_by == "admin"
        assert event.old_value == 5.0
        assert event.new_value == 7.0

    def test_config_change_event_optional_fields(self, utc_now):
        """ConfigChangeEvent 선택 필드 테스트."""
        event = ConfigChangeEvent(
            timestamp=utc_now,
            changed_by="system",
        )
        assert event.old_value is None
        assert event.new_value is None
        assert event.version_tag == ""

    def test_config_change_event_roundtrip(self, utc_now):
        """ConfigChangeEvent 라운드트립 테스트."""
        event = ConfigChangeEvent(
            timestamp=utc_now,
            changed_by="admin",
            old_value={"key": "old"},
            new_value={"key": "new"},
            approval_log="Approved by CEO",
        )
        json_str = event.model_dump_json()
        restored = ConfigChangeEvent.model_validate_json(json_str)
        assert restored.changed_by == event.changed_by


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """통합 테스트."""

    def test_full_trading_flow(self, utc_now, sample_broker_order, sample_order_sizing):
        """전체 거래 흐름 테스트."""
        # 1. MarketSnapshot 생성
        snapshot = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
        )

        # 2. TradeIdea 생성
        idea = TradeIdea(
            symbol="005930",
            side=Side.BUY,
            confidence=0.8,
            entry=70000.0,
            tp=75000.0,
            sl=68000.0,
        )

        # 3. ApprovedOrderPlan 생성
        plan = ApprovedOrderPlan(
            trace_id=idea.trace_id,
            decision="APPROVED",
            mode=TradingMode.PAPER,
            sizing=sample_order_sizing,
            order=sample_broker_order,
        )

        # 4. OrderResult 생성
        result = OrderResult(
            trace_id=plan.trace_id,
            broker_order_id="ORD001",
            status=OrderStatus.FILLED,
        )

        # 모든 trace_id가 연결되어 있는지 확인
        assert plan.trace_id == idea.trace_id
        assert result.trace_id == plan.trace_id

    def test_enum_serialization_in_models(self, utc_now):
        """모델 내 Enum 직렬화 테스트."""
        snapshot = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
        )
        data = snapshot.model_dump()
        assert data["venue"] == "KR"

        json_str = snapshot.model_dump_json()
        assert '"venue":"KR"' in json_str or '"venue": "KR"' in json_str

    def test_optional_fields_serialization(self, utc_now):
        """Optional 필드 직렬화 테스트."""
        snapshot = MarketSnapshot(
            ts=utc_now,
            venue=Venue.KR,
            symbol="005930",
            price=70000.0,
            bid=None,
            ask=None,
            candle=None,
            features=None,
        )
        json_str = snapshot.model_dump_json()
        restored = MarketSnapshot.model_validate_json(json_str)
        assert restored.bid is None
        assert restored.ask is None
        assert restored.candle is None
        assert restored.features is None
