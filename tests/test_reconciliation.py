from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from engine.execution_oms import ExecutionOMS
from engine.portfolio import PortfolioManager
from engine.reconciliation import ReconciliationEngine
from schemas.models import (
    AlertSeverity,
    ApprovedOrderPlan,
    BrokerOrder,
    OrderSizing,
    Side,
    TradingMode,
)


@pytest.fixture
def portfolio() -> PortfolioManager:
    return PortfolioManager(initial_cash=10_000_000)


@pytest.fixture
def oms(tmp_path, portfolio: PortfolioManager) -> ExecutionOMS:
    return ExecutionOMS(db_path=tmp_path / "test.db", portfolio=portfolio)


def _make_mock_broker(
    positions=None,
    cash=10_000_000.0,
    orders=None,
    fills=None,
    fills_error=None,
):
    broker = MagicMock()
    broker.get_balance.return_value = {
        "positions": positions or [],
        "cash": cash,
        "total_value": cash,
    }
    broker.get_order_status.return_value = orders or []
    if fills_error:
        broker.get_fills.side_effect = fills_error
    else:
        broker.get_fills.return_value = fills or []
    return broker


def _make_plan(symbol="005930", side=Side.BUY, qty=10, price=70000):
    trace_id = uuid4()
    return ApprovedOrderPlan(
        trace_id=trace_id,
        mode=TradingMode.PAPER,
        sizing=OrderSizing(qty=qty, notional=qty * price, weight_pct=5.0),
        order=BrokerOrder(symbol=symbol, side=side, qty=qty, price=price),
    )


def _submit_submitted_order(oms: ExecutionOMS, broker_order_id: str) -> None:
    created = oms.submit_order(_make_plan())
    _ = oms.on_ack(str(created.trace_id), broker_order_id)


def test_perfect_match(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    portfolio.apply_fill("005930", Side.BUY, 10, 70000)
    internal_cash = portfolio.get_portfolio().cash
    broker = _make_mock_broker(
        positions=[{"symbol": "005930", "qty": 10}],
        cash=internal_cash,
        orders=[],
    )
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is False
    assert engine.is_reconciled() is True
    assert result.mismatches == []


def test_position_qty_mismatch(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    portfolio.apply_fill("005930", Side.BUY, 15, 70000)
    internal_cash = portfolio.get_portfolio().cash
    broker = _make_mock_broker(
        positions=[{"symbol": "005930", "qty": 10}],
        cash=internal_cash,
    )
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(m.severity == AlertSeverity.CRITICAL for m in result.mismatches)


def test_position_missing_from_broker(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    portfolio.apply_fill("005930", Side.BUY, 10, 70000)
    broker = _make_mock_broker(
        positions=[],
        cash=portfolio.get_portfolio().cash,
    )
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    engine.reconcile()

    assert engine.is_frozen() is True


def test_extra_position_on_broker(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker(
        positions=[{"symbol": "005930", "qty": 10}],
        cash=portfolio.get_portfolio().cash,
    )
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    engine.reconcile()

    assert engine.is_frozen() is True


def test_cash_within_tolerance(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    broker = _make_mock_broker(cash=10_000_050.0)
    engine = ReconciliationEngine(
        portfolio=portfolio,
        oms=oms,
        broker=broker,
        cash_tolerance=100.0,
    )

    result = engine.reconcile()

    assert engine.is_frozen() is False
    assert not any(m.field == "cash" for m in result.mismatches)


def test_cash_beyond_tolerance(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    broker = _make_mock_broker(cash=10_000_200.0)
    engine = ReconciliationEngine(
        portfolio=portfolio,
        oms=oms,
        broker=broker,
        cash_tolerance=100.0,
    )

    result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(
        m.field == "cash" and m.severity == AlertSeverity.HIGH
        for m in result.mismatches
    )


def test_open_order_unknown_broker(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker(orders=[{"odno": "12345"}])
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    with patch("engine.reconciliation.time.sleep", return_value=None):
        result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(
        m.field.startswith("open_order.unknown_broker") for m in result.mismatches
    )


def test_open_order_unknown_internal(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    _submit_submitted_order(oms, broker_order_id="99999")
    broker = _make_mock_broker(orders=[])
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    with patch("engine.reconciliation.time.sleep", return_value=None):
        result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(
        m.field.startswith("open_order.unknown_internal") for m in result.mismatches
    )


def test_new_order_skipped(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    _ = oms.submit_order(_make_plan())
    broker = _make_mock_broker(orders=[])
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is False
    assert not any(m.field.startswith("open_order") for m in result.mismatches)


def test_paper_mode_valid(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=None)

    result = engine.reconcile()

    assert engine.is_reconciled() is True
    assert engine.is_frozen() is False
    assert result.mismatches == []


def test_paper_mode_negative_cash(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    portfolio._cash = -1.0
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=None)

    result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(m.severity == AlertSeverity.CRITICAL for m in result.mismatches)


def test_re_reconcile_clears_freeze(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    portfolio.apply_fill("005930", Side.BUY, 10, 70000)
    internal_cash = portfolio.get_portfolio().cash
    broker = _make_mock_broker(
        positions=[{"symbol": "005930", "qty": 1}],
        cash=internal_cash,
    )
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    first = engine.reconcile()
    assert engine.is_frozen() is True
    assert first.mismatches

    broker.get_balance.return_value = {
        "positions": [{"symbol": "005930", "qty": 10}],
        "cash": internal_cash,
        "total_value": internal_cash,
    }
    second = engine.reconcile()

    assert engine.is_frozen() is False
    assert engine.is_reconciled() is True
    assert second.mismatches == []


def test_needs_reconciliation_interval(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker()
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    _ = engine.reconcile()

    assert engine.needs_reconciliation() is False
    engine._last_reconciled_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    assert engine.needs_reconciliation() is True


def test_unfreeze_resets_reconciled(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker()
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    _ = engine.reconcile()
    engine.unfreeze()

    assert engine.is_reconciled() is False
    assert engine.is_frozen() is False
    assert engine.needs_reconciliation() is True


def test_broker_api_failure(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    broker = _make_mock_broker()
    broker.get_balance.side_effect = RuntimeError("balance failed")
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    with pytest.raises(RuntimeError, match="balance failed"):
        engine.reconcile()

    assert engine.is_reconciled() is False


def test_fill_check_disabled(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    broker = _make_mock_broker()
    engine = ReconciliationEngine(
        portfolio=portfolio,
        oms=oms,
        broker=broker,
        fill_check_mode="disabled",
    )

    _ = engine.reconcile()

    broker.get_fills.assert_not_called()


def test_fill_check_warn_with_error(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker(fills_error=RuntimeError("fills unavailable"))
    engine = ReconciliationEngine(
        portfolio=portfolio,
        oms=oms,
        broker=broker,
        fill_check_mode="warn",
    )

    result = engine.reconcile()

    assert engine.is_frozen() is False
    assert engine.is_reconciled() is True
    assert not any(m.field == "fills.fetch" for m in result.mismatches)


def test_check_trading_allowed(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=None)

    engine._is_reconciled = True
    engine._frozen = False
    assert engine.check_trading_allowed() is True

    engine._is_reconciled = False
    engine._frozen = False
    assert engine.check_trading_allowed() is False

    engine._is_reconciled = True
    engine._frozen = True
    assert engine.check_trading_allowed() is False


def test_order_retry_clears_transient(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    _submit_submitted_order(oms, broker_order_id="12345")
    broker = _make_mock_broker(
        orders=[{"odno": "99999"}],
    )
    broker.get_order_status.side_effect = [
        [{"odno": "99999"}],
        [{"odno": "12345"}],
    ]
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    with patch("engine.reconciliation.time.sleep", return_value=None):
        result = engine.reconcile()

    assert engine.is_frozen() is False
    assert engine.is_reconciled() is True
    assert not any(m.field.startswith("open_order") for m in result.mismatches)


def test_broker_cash_missing(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    broker = _make_mock_broker()
    broker.get_balance.return_value = {
        "positions": [],
        "total_value": 10_000_000.0,
    }
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(m.field == "cash.missing" for m in result.mismatches)


def test_odno_none_skipped(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    broker = _make_mock_broker(orders=[{"odno": None}])
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is False
    assert not any(".None" in m.field for m in result.mismatches)
    assert not any(m.field.startswith("open_order") for m in result.mismatches)


def test_symbol_normalization(portfolio: PortfolioManager, oms: ExecutionOMS) -> None:
    portfolio.apply_fill("005930", Side.BUY, 10, 70000)
    internal_cash = portfolio.get_portfolio().cash
    broker = _make_mock_broker(
        positions=[{"symbol": " 005930 ", "qty": 10}],
        cash=internal_cash,
    )
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is False
    assert result.mismatches == []


def test_order_retry_refreshes_internal(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    class _OrderStub:
        def __init__(self, broker_order_id: str) -> None:
            self.broker_order_id = broker_order_id

    broker = _make_mock_broker(orders=[{"odno": "B"}])
    broker.get_order_status.side_effect = [
        [{"odno": "B"}],
        [{"odno": "B"}],
    ]

    oms.get_open_orders = MagicMock(side_effect=[[_OrderStub("A")], [_OrderStub("B")]])

    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    with patch("engine.reconciliation.time.sleep", return_value=None):
        result = engine.reconcile()

    assert engine.is_frozen() is False
    assert engine.is_reconciled() is True
    assert not any(m.field.startswith("open_order") for m in result.mismatches)


def test_reconcile_exception_resets_state(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker()
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    _ = engine.reconcile()
    assert engine.is_reconciled() is True

    broker.get_balance.side_effect = RuntimeError("fetch failed")
    with pytest.raises(RuntimeError, match="fetch failed"):
        engine.reconcile()

    assert engine.is_reconciled() is False
    assert engine.check_trading_allowed() is False


def test_broker_invalid_balance_payload(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker()
    broker.get_balance.return_value = "invalid"
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(m.field == "balance.invalid_payload" for m in result.mismatches)


def test_broker_invalid_orders_payload(
    portfolio: PortfolioManager, oms: ExecutionOMS
) -> None:
    broker = _make_mock_broker()
    broker.get_order_status.return_value = None
    engine = ReconciliationEngine(portfolio=portfolio, oms=oms, broker=broker)

    result = engine.reconcile()

    assert engine.is_frozen() is True
    assert any(m.field == "orders.invalid_payload" for m in result.mismatches)
