from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from adapters.kis_adapter import KISAPIError
from engine.kill_switch import KillSwitchLevel
from agents.pipeline import PipelineResult, TradingPipeline
from config.settings import (
    AppConfig,
    LLMTier,
    LiteLLMConfig,
    MAStrategy,
    RiskPolicyConfig,
    Settings,
    StrategyConfig,
)
from schemas.models import (
    ApprovedOrderPlan,
    BrokerOrder,
    Horizon,
    MarketSnapshot,
    OrderSizing,
    OrderStatus,
    Rejected,
    RiskCheckResult,
    RiskPolicy,
    Side,
    TradingMode,
    TradeIdea,
    Venue,
)


def _make_settings(mode: TradingMode, tmp_dir: Path) -> Settings:
    return Settings(
        app=AppConfig(
            trading_mode=mode,
            venue=Venue.KR,
            db_path=str(tmp_dir / "test.db"),
            log_path=str(tmp_dir / "logs"),
        ),
        risk=RiskPolicyConfig(
            profiles={"defensive": RiskPolicy()},
            active_profile="defensive",
        ),
        strategy=StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        ),
        litellm=LiteLLMConfig(
            tiers={"smart_a": LLMTier(model="fake-model")},
            default_tier="smart_a",
        ),
    )


def _make_snapshot(symbol: str = "005930") -> MarketSnapshot:
    return MarketSnapshot(
        ts=datetime.now(timezone.utc),
        venue=Venue.KR,
        symbol=symbol,
        price=70000.0,
        bid=69900.0,
        ask=70100.0,
        volume=100000,
    )


def _make_idea(symbol: str = "005930", side: Side = Side.BUY) -> TradeIdea:
    return TradeIdea(
        symbol=symbol,
        side=side,
        confidence=0.9,
        horizon=Horizon.SWING,
        thesis="test",
        entry=70000.0,
        tp=72100.0,
        sl=68600.0,
    )


def _make_approved(
    idea: TradeIdea, mode: TradingMode, qty: int = 10
) -> ApprovedOrderPlan:
    return ApprovedOrderPlan(
        trace_id=idea.trace_id,
        mode=mode,
        sizing=OrderSizing(qty=qty, notional=qty * idea.entry, weight_pct=5.0),
        risk_checks=[RiskCheckResult(rule_name="test", passed=True, detail="")],
        order=BrokerOrder(
            symbol=idea.symbol,
            side=idea.side,
            qty=qty,
            price=idea.entry,
            order_type="LIMIT",
        ),
    )


def _make_rejected(idea: TradeIdea, reason: str = "blocked") -> Rejected:
    return Rejected(trace_id=idea.trace_id, reason=reason, risk_checks=[])


def test_pipeline_result_defaults() -> None:
    result = PipelineResult()

    assert result.run_id
    assert result.timestamp.tzinfo is not None
    assert result.snapshots_collected == 0
    assert result.ideas_generated == 0
    assert result.orders_approved == 0
    assert result.orders_submitted == 0
    assert result.orders_filled == 0
    assert result.errors == []


def test_paused_mode_skips(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.PAUSED, tmp_path)
    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings)

    try:
        result = pipeline.run_once(symbols=["005930"])
    finally:
        pipeline.close()

    assert result.orders_submitted == 0
    assert result.orders_filled == 0
    assert any("PAUSED" in err for err in result.errors)


def test_no_data_returns_early(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.PAPER, tmp_path)
    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)

    with patch("agents.pipeline.DataHub") as mock_data_hub:
        mock_data_hub.return_value.collect.return_value = []
        try:
            result = pipeline.run_once(symbols=["005930"])
        finally:
            pipeline.close()

    assert result.snapshots_collected == 0
    assert result.orders_submitted == 0
    assert any("No snapshots" in err for err in result.errors)


def test_dry_run_e2e(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.PAPER, tmp_path)
    idea = _make_idea()
    approved = _make_approved(idea, TradingMode.PAPER, qty=7)
    snapshots = [_make_snapshot()]

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)
    pipeline._strategy_hub.generate = MagicMock(return_value=[idea])
    pipeline._risk_gate.evaluate = MagicMock(return_value=approved)

    with patch("agents.pipeline.DataHub") as mock_data_hub:
        mock_data_hub.return_value.collect.return_value = snapshots
        result = pipeline.run_once(symbols=["005930"])

    order = pipeline._oms.get_order(str(approved.trace_id))
    pipeline.close()

    assert result.snapshots_collected == 1
    assert result.ideas_generated == 1
    assert result.orders_approved == 1
    assert result.orders_submitted == 1
    assert result.orders_filled == 0
    assert order is not None
    assert order.status == OrderStatus.SUBMITTED


def test_all_rejected_zero_orders(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.PAPER, tmp_path)
    idea = _make_idea()

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)
    pipeline._strategy_hub.generate = MagicMock(return_value=[idea])
    pipeline._risk_gate.evaluate = MagicMock(
        return_value=_make_rejected(idea, "risk fail")
    )

    with patch("agents.pipeline.DataHub") as mock_data_hub:
        mock_data_hub.return_value.collect.return_value = [_make_snapshot()]
        try:
            result = pipeline.run_once(symbols=["005930"])
        finally:
            pipeline.close()

    assert result.orders_approved == 0
    assert result.orders_submitted == 0
    assert any("Rejected" in err for err in result.errors)


def test_with_kis_submit_and_fill(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.REAL, tmp_path)
    idea = _make_idea()
    approved = _make_approved(idea, TradingMode.REAL, qty=10)
    kis_mock = MagicMock()
    kis_mock.get_balance.return_value = {
        "positions": [],
        "cash": 10_000_000.0,
        "total_value": 10_000_000.0,
    }
    kis_mock.get_order_status.return_value = []
    kis_mock.submit_order.return_value = {
        "odno": "OD12345",
        "orgno": "ORG01",
        "ord_tmd": "093001",
        "raw": {},
    }
    kis_mock.get_fills.side_effect = [
        [],
        [
            {
                "odno": "OD12345",
                "ccld_qty": 10,
                "ccld_unpr": 70000,
                "ccld_dttm": "2026-03-02T01:30:00+00:00",
            }
        ],
    ]

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings, kis_adapter=kis_mock)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)
    pipeline._strategy_hub.generate = MagicMock(return_value=[idea])
    pipeline._risk_gate.evaluate = MagicMock(return_value=approved)

    with (
        patch("agents.pipeline.DataHub") as mock_data_hub,
        patch("agents.pipeline.time.sleep", return_value=None),
    ):
        mock_data_hub.return_value.collect.return_value = [_make_snapshot()]
        result = pipeline.run_once(symbols=["005930"])

    order = pipeline._oms.get_order(str(approved.trace_id))
    pipeline.close()

    assert result.orders_submitted == 1
    assert result.orders_filled == 1
    assert result.errors == []
    kis_mock.submit_order.assert_called_once()
    assert kis_mock.get_fills.call_count >= 1
    assert order is not None
    assert order.status == OrderStatus.FILLED
    assert len(order.fills) == 1


def test_circuit_breaker_halts_batch_on_transient_errors(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.REAL, tmp_path)
    ideas = [_make_idea(symbol=f"00593{i}") for i in range(6)]
    approved = [_make_approved(idea, TradingMode.REAL, qty=1) for idea in ideas]
    kis_mock = MagicMock()
    kis_mock.get_balance.return_value = {
        "positions": [],
        "cash": 10_000_000.0,
        "total_value": 10_000_000.0,
    }
    kis_mock.get_order_status.return_value = []
    request = httpx.Request("POST", "https://example.com/order")
    kis_mock.submit_order.side_effect = httpx.RequestError(
        "network down", request=request
    )

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings, kis_adapter=kis_mock)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)
    pipeline._strategy_hub.generate = MagicMock(return_value=ideas)
    pipeline._risk_gate.evaluate = MagicMock(side_effect=approved)

    with patch("agents.pipeline.DataHub") as mock_data_hub:
        mock_data_hub.return_value.collect.return_value = [_make_snapshot()]
        result = pipeline.run_once(symbols=["005930"])

    try:
        assert result.orders_approved == 6
        assert result.orders_submitted == 5
        assert kis_mock.submit_order.call_count == 5
        assert any(
            "Circuit opened - halting remaining orders" in err for err in result.errors
        )
    finally:
        pipeline.close()


def test_deterministic_error_does_not_halt_batch(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.REAL, tmp_path)
    ideas = [_make_idea(symbol=f"00066{i}") for i in range(3)]
    approved = [_make_approved(idea, TradingMode.REAL, qty=1) for idea in ideas]
    kis_mock = MagicMock()
    kis_mock.get_balance.return_value = {
        "positions": [],
        "cash": 10_000_000.0,
        "total_value": 10_000_000.0,
    }
    kis_mock.get_order_status.return_value = []
    kis_mock.submit_order.side_effect = [
        KISAPIError("EGW001", "invalid input"),
        KISAPIError("EGW001", "invalid input"),
        KISAPIError("EGW001", "invalid input"),
    ]

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings, kis_adapter=kis_mock)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)
    pipeline._strategy_hub.generate = MagicMock(return_value=ideas)
    pipeline._risk_gate.evaluate = MagicMock(side_effect=approved)

    with patch("agents.pipeline.DataHub") as mock_data_hub:
        mock_data_hub.return_value.collect.return_value = [_make_snapshot()]
        result = pipeline.run_once(symbols=["005930"])

    try:
        assert result.orders_approved == 3
        assert result.orders_submitted == 3
        assert kis_mock.submit_order.call_count == 3
        assert not any("Circuit opened" in err for err in result.errors)
    finally:
        pipeline.close()


def test_pipeline_recovery_failure_blocks_trading(tmp_path: Path) -> None:
    settings = _make_settings(TradingMode.PAPER, tmp_path)

    with (
        patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")),
        patch(
            "agents.pipeline.ExecutionOMS.recover_open_orders",
            side_effect=RuntimeError("recovery failed"),
        ),
    ):
        pipeline = TradingPipeline(settings)

    try:
        result = pipeline.run_once(symbols=["005930"])
        assert result.orders_submitted == 0
        assert any(
            "OMS recovery failed - trading blocked" in err for err in result.errors
        )
    finally:
        pipeline.close()


def test_pipeline_kill_switch_blocks_trading(tmp_path: Path) -> None:
    """킬 스위치 활성화 시 run_once가 즉시 반환된다."""
    settings = _make_settings(TradingMode.PAPER, tmp_path)

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings)

    pipeline._kill_switch.activate(KillSwitchLevel.PAUSE, reason="test block")

    try:
        result = pipeline.run_once(symbols=["005930"])
    finally:
        pipeline.close()

    assert any("Kill Switch" in err for err in result.errors)
    assert result.snapshots_collected == 0


def test_pipeline_monitor_stop_activates_kill_switch(tmp_path: Path) -> None:
    """Monitor STOP 알림 발생 시 킬 스위치가 자동 활성화된다."""
    settings = _make_settings(TradingMode.PAPER, tmp_path)

    with patch("agents.pipeline.AnalystAgent", side_effect=Exception("no LLM")):
        pipeline = TradingPipeline(settings)

    pipeline._market_calendar.is_within_trading_hours = MagicMock(return_value=True)

    # 왜(why): MDD 위반 포트폴리오를 주입하여 Monitor가 STOP 알림을 발행하게 한다.
    from schemas.models import Portfolio
    mdd_portfolio = Portfolio(
        positions=[], cash=10_000_000, total_value=10_000_000,
        daily_pnl=0, mdd=7.0, updated_at=datetime.now(timezone.utc),
    )
    pipeline._monitor._portfolio_fn = lambda: mdd_portfolio

    with (
        patch("agents.pipeline.DataHub") as mock_data_hub,
    ):
        mock_data_hub.return_value.collect.return_value = [_make_snapshot()]
        # 왜(why): 최소 1개 아이디어를 생성해야 monitor check까지 진행된다.
        pipeline._strategy_hub.generate = MagicMock(return_value=[_make_idea()])
        # 왜(why): 리스크 게이트가 거부하도록 설정하여 주문 실행을 건너뛴다.
        pipeline._risk_gate.evaluate = MagicMock(return_value=_make_rejected(_make_idea()))
        result = pipeline.run_once(symbols=["005930"])

    try:
        assert pipeline._kill_switch.is_active
        assert pipeline._kill_switch.level == KillSwitchLevel.PAUSE
        assert "MDD" in pipeline._kill_switch.reason
    finally:
        pipeline.close()
