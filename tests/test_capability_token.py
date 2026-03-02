"""tests/test_capability_token.py - CapabilityTokenManager 및 Phase 1b 통합 테스트."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from engine.capability_token import CapabilityTokenManager
from engine.execution_oms import ExecutionOMS
from engine.risk_gate import RiskGate
from schemas.models import (
    ApprovedOrderPlan,
    BrokerOrder,
    Horizon,
    OrderSizing,
    Portfolio,
    RiskPolicy,
    Side,
    TradingMode,
    TradeIdea,
)


def _decode_b64url(encoded: str) -> bytes:
    padding = "=" * ((4 - (len(encoded) % 4)) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def _make_plan(
    *,
    trace_id: UUID | None = None,
    symbol: str = "005930",
    side: Side = Side.BUY,
    qty: int = 10,
    price: float | None = 70000.0,
    notional: float | None = None,
    capability_token: str | None = None,
) -> ApprovedOrderPlan:
    resolved_notional = (
        notional if notional is not None else float(qty) * float(price or 0.0)
    )
    return ApprovedOrderPlan(
        trace_id=trace_id or uuid4(),
        mode=TradingMode.PAPER,
        sizing=OrderSizing(qty=qty, notional=resolved_notional, weight_pct=5.0),
        risk_checks=[],
        order=BrokerOrder(symbol=symbol, side=side, qty=qty, price=price),
        capability_token=capability_token,
    )


@pytest.fixture
def token_manager() -> CapabilityTokenManager:
    return CapabilityTokenManager(
        secret_key=b"phase1b-test-secret", default_ttl_seconds=60
    )


@pytest.fixture
def sample_plan() -> ApprovedOrderPlan:
    return _make_plan()


@pytest.fixture
def policy() -> RiskPolicy:
    return RiskPolicy()


@pytest.fixture
def now_trading_hours() -> datetime:
    return datetime(2026, 3, 2, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def data_asof(now_trading_hours: datetime) -> datetime:
    return now_trading_hours - timedelta(minutes=10)


@pytest.fixture
def portfolio() -> Portfolio:
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
    return TradeIdea(
        symbol="005930",
        side=Side.BUY,
        confidence=0.8,
        horizon=Horizon.SWING,
        entry=70000.0,
        tp=75000.0,
        sl=65000.0,
    )


class TestTokenGeneration:
    def test_generate_returns_valid_format(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        assert token.count(".") == 1
        payload_part, signature_part = token.split(".")
        assert _decode_b64url(payload_part)
        assert _decode_b64url(signature_part)

    def test_generate_contains_expected_claims(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        payload_part, _ = token.split(".")
        payload = json.loads(_decode_b64url(payload_part).decode("ascii"))
        assert set(payload.keys()) == {
            "exp",
            "iat",
            "jti",
            "order_price",
            "order_qty",
            "order_side",
            "order_symbol",
            "order_type",
            "sizing_notional",
            "time_in_force",
            "trace_id",
        }
        assert payload["order_price"] == "70000.0"
        assert payload["order_qty"] == "10"
        assert payload["order_side"] == "BUY"
        assert payload["order_symbol"] == "005930"
        assert payload["sizing_notional"] == "700000.0"
        assert payload["trace_id"] == str(sample_plan.trace_id)

    def test_generate_different_nonces(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        first = token_manager.generate(sample_plan)
        second = token_manager.generate(sample_plan)
        first_payload = json.loads(_decode_b64url(first.split(".")[0]).decode("ascii"))
        second_payload = json.loads(
            _decode_b64url(second.split(".")[0]).decode("ascii")
        )
        assert first != second
        assert first_payload["jti"] != second_payload["jti"]


class TestTokenVerification:
    def test_verify_valid_token(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        result = token_manager.verify(token, sample_plan)
        assert result.valid is True

    def test_verify_forged_signature(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        payload_part, signature_part = token.split(".")
        signature = bytearray(_decode_b64url(signature_part))
        signature[0] ^= 0xFF
        forged_signature = (
            base64.urlsafe_b64encode(bytes(signature)).rstrip(b"=").decode("ascii")
        )
        forged_token = f"{payload_part}.{forged_signature}"
        result = token_manager.verify(forged_token, sample_plan)
        assert result.valid is False
        assert "서명" in result.reason

    @pytest.mark.parametrize("malformed", ["not-a-valid-token", "garbage"])
    def test_verify_malformed_token(
        self,
        token_manager: CapabilityTokenManager,
        sample_plan: ApprovedOrderPlan,
        malformed: str,
    ) -> None:
        result = token_manager.verify(malformed, sample_plan)
        assert result.valid is False


class TestTokenExpiration:
    def test_verify_expired_token(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        with patch("time.time", return_value=1_700_000_000):
            token = token_manager.generate(sample_plan, ttl_seconds=1)
        with patch("time.time", return_value=1_700_000_010):
            result = token_manager.verify(token, sample_plan)
        assert result.valid is False
        assert "만료" in result.reason

    def test_verify_within_clock_skew(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        with patch("time.time", return_value=1_700_000_000):
            token = token_manager.generate(sample_plan, ttl_seconds=1)
        with patch("time.time", return_value=1_700_000_005):
            result = token_manager.verify(token, sample_plan)
        assert result.valid is True


class TestPayloadTampering:
    def test_verify_tampered_qty(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        tampered = _make_plan(
            trace_id=sample_plan.trace_id,
            qty=11,
            price=sample_plan.order.price,
            symbol=sample_plan.order.symbol,
            side=sample_plan.order.side,
            notional=770000.0,
        )
        result = token_manager.verify(token, tampered)
        assert result.valid is False
        assert "불일치" in result.reason

    def test_verify_tampered_price(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        tampered = _make_plan(
            trace_id=sample_plan.trace_id,
            qty=sample_plan.order.qty,
            price=71000.0,
            symbol=sample_plan.order.symbol,
            side=sample_plan.order.side,
            notional=710000.0,
        )
        result = token_manager.verify(token, tampered)
        assert result.valid is False

    def test_verify_tampered_symbol(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        tampered = _make_plan(
            trace_id=sample_plan.trace_id,
            qty=sample_plan.order.qty,
            price=sample_plan.order.price,
            symbol="000660",
            side=sample_plan.order.side,
            notional=sample_plan.sizing.notional,
        )
        result = token_manager.verify(token, tampered)
        assert result.valid is False

    def test_verify_tampered_order_type(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        """order_type 변조 → 거부."""
        token = token_manager.generate(sample_plan)
        tampered = _make_plan(
            trace_id=sample_plan.trace_id,
            qty=sample_plan.order.qty,
            price=sample_plan.order.price,
            symbol=sample_plan.order.symbol,
            side=sample_plan.order.side,
            notional=sample_plan.sizing.notional,
        )
        tampered_order = tampered.order.model_copy(update={"order_type": "MARKET"})
        tampered = tampered.model_copy(update={"order": tampered_order})
        result = token_manager.verify(token, tampered)
        assert result.valid is False
        assert "불일치" in result.reason


class TestNonceReplay:
    def test_verify_double_use_rejected(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        token = token_manager.generate(sample_plan)
        first = token_manager.verify(token, sample_plan)
        second = token_manager.verify(token, sample_plan)
        assert first.valid is True
        assert second.valid is False
        assert "재사용" in second.reason

    def test_nonce_cleanup(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        with patch("time.time", return_value=1_700_000_000):
            old_token = token_manager.generate(sample_plan, ttl_seconds=1)
        old_payload = json.loads(
            _decode_b64url(old_token.split(".")[0]).decode("ascii")
        )
        old_jti = old_payload["jti"]

        with patch("time.time", return_value=1_700_000_000):
            old_result = token_manager.verify(old_token, sample_plan)
        assert old_result.valid is True
        assert old_jti in token_manager._used_nonces

        with patch("time.time", return_value=1_700_000_020):
            new_token = token_manager.generate(sample_plan, ttl_seconds=60)
            new_result = token_manager.verify(new_token, sample_plan)
        assert new_result.valid is True
        assert old_jti not in token_manager._used_nonces


class TestClaimValidation:
    """클레임 검증 엣지 케이스 테스트."""

    def test_iat_in_future_rejected(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        """iat가 미래 (clock skew 초과) → 거부."""
        with patch("time.time", return_value=1_700_000_100):
            token = token_manager.generate(sample_plan, ttl_seconds=60)
        with patch("time.time", return_value=1_700_000_000):
            result = token_manager.verify(token, sample_plan)
        assert result.valid is False
        assert "미래" in result.reason

    def test_excessive_ttl_rejected(
        self, token_manager: CapabilityTokenManager, sample_plan: ApprovedOrderPlan
    ) -> None:
        """TTL이 max_ttl(default_ttl*2=120s) 초과 → 거부."""
        with patch("time.time", return_value=1_700_000_000):
            token = token_manager.generate(sample_plan, ttl_seconds=200)
        with patch("time.time", return_value=1_700_000_000):
            result = token_manager.verify(token, sample_plan)
        assert result.valid is False
        assert "TTL" in result.reason


class TestIntegration:
    def test_risk_gate_generates_token(
        self,
        token_manager: CapabilityTokenManager,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        gate = RiskGate(token_manager=token_manager)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        assert result.capability_token is not None

    def test_oms_rejects_missing_token(
        self,
        token_manager: CapabilityTokenManager,
        tmp_path: Path,
        sample_plan: ApprovedOrderPlan,
    ) -> None:
        with ExecutionOMS(
            db_path=tmp_path / "orders.db", token_manager=token_manager
        ) as oms:
            with pytest.raises(ValueError, match="capability_token 필수"):
                oms.submit_order(sample_plan)

    def test_e2e_risk_gate_to_oms(
        self,
        token_manager: CapabilityTokenManager,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
        tmp_path: Path,
    ) -> None:
        gate = RiskGate(token_manager=token_manager)
        approved = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(approved, ApprovedOrderPlan)
        assert approved.capability_token is not None

        with ExecutionOMS(
            db_path=tmp_path / "e2e.db", token_manager=token_manager
        ) as oms:
            result = oms.submit_order(approved)
        assert result.trace_id == approved.trace_id


class TestBackwardCompatibility:
    def test_risk_gate_without_manager(
        self,
        buy_idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        now_trading_hours: datetime,
        data_asof: datetime,
    ) -> None:
        gate = RiskGate(token_manager=None)
        result = gate.evaluate(
            buy_idea,
            portfolio,
            policy,
            TradingMode.PAPER,
            now_trading_hours,
            data_asof,
        )
        assert isinstance(result, ApprovedOrderPlan)
        assert result.capability_token is None

    def test_oms_without_manager(
        self, tmp_path: Path, sample_plan: ApprovedOrderPlan
    ) -> None:
        with ExecutionOMS(db_path=tmp_path / "compat.db", token_manager=None) as oms:
            result = oms.submit_order(sample_plan)
        assert result.trace_id == sample_plan.trace_id
