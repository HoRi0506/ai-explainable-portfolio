import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from config.settings import load_settings
from agents.analyst_agent import AnalystAgent, AnalystStrategy
from schemas.models import MarketSnapshot, Side, Venue


def _make_snapshot(symbol: str = "005930", price: float = 70000.0) -> MarketSnapshot:
    return MarketSnapshot(
        ts=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        venue=Venue.KR,
        symbol=symbol,
        price=price,
        bid=price - 100,
        ask=price + 100,
        volume=1_000_000,
    )


def _mock_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


def test_valid_json_generates_trade_idea() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)
    snapshot = _make_snapshot()

    payload = {
        "ideas": [
            {
                "symbol": "005930",
                "side": "BUY",
                "confidence": 0.82,
                "horizon": "SWING",
                "thesis": "Momentum breakout",
                "entry": 70000.0,
                "tp": 72100.0,
                "sl": 68600.0,
                "constraints": {"note": "unit-test"},
            }
        ]
    }

    with patch(
        "agents.analyst_agent.litellm.completion", return_value=_mock_response(payload)
    ) as mocked:
        ideas = agent.analyze([snapshot])

    assert len(ideas) == 1
    assert ideas[0].symbol == "005930"
    assert ideas[0].side == Side.BUY
    assert ideas[0].confidence == 0.82
    assert ideas[0].constraints is not None
    assert ideas[0].constraints["venue"] == "KR"
    assert ideas[0].constraints["data_asof"] == snapshot.ts.isoformat()
    assert mocked.call_args.kwargs["model"] == settings.litellm.tiers["smart_a"].model
    assert (
        mocked.call_args.kwargs["timeout"]
        == settings.litellm.tiers["smart_a"].timeout_sec
    )


def test_invalid_json_returns_hold() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)

    bad_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]
    )

    with patch("agents.analyst_agent.litellm.completion", return_value=bad_response):
        ideas = agent.analyze([_make_snapshot()])

    assert ideas == []


def test_llm_timeout_returns_hold_with_one_retry() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)

    with (
        patch(
            "agents.analyst_agent.litellm.completion",
            side_effect=TimeoutError("timeout"),
        ) as mocked,
        patch("agents.analyst_agent.time.sleep") as mocked_sleep,
    ):
        ideas = agent.analyze([_make_snapshot()])

    assert ideas == []
    assert mocked.call_count == 2
    mocked_sleep.assert_called_once_with(2)


def test_low_confidence_is_filtered_out() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)
    payload = {
        "ideas": [
            {
                "symbol": "005930",
                "side": "BUY",
                "confidence": 0.59,
                "horizon": "SWING",
                "thesis": "Weak",
                "entry": 70000.0,
                "tp": 72100.0,
                "sl": 68600.0,
                "constraints": {},
            },
            {
                "symbol": "005930",
                "side": "SELL",
                "confidence": 0.7,
                "horizon": "INTRADAY",
                "thesis": "Strong",
                "entry": 70000.0,
                "tp": 67900.0,
                "sl": 71400.0,
                "constraints": {},
            },
        ]
    }

    with patch(
        "agents.analyst_agent.litellm.completion", return_value=_mock_response(payload)
    ):
        ideas = agent.analyze([_make_snapshot()])

    assert len(ideas) == 1
    assert ideas[0].side == Side.SELL


def test_empty_ideas_returns_empty_list() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)

    with patch(
        "agents.analyst_agent.litellm.completion",
        return_value=_mock_response({"ideas": []}),
    ):
        ideas = agent.analyze([_make_snapshot()])

    assert ideas == []


def test_pydantic_validation_failure_returns_hold() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)

    payload = {
        "ideas": [
            {
                "symbol": "005930",
                "side": "HOLD",
                "confidence": 0.8,
                "horizon": "SWING",
                "thesis": "Invalid side",
                "entry": 70000.0,
                "tp": 72100.0,
                "sl": 68600.0,
                "constraints": {},
            }
        ]
    }

    with patch(
        "agents.analyst_agent.litellm.completion", return_value=_mock_response(payload)
    ):
        ideas = agent.analyze([_make_snapshot()])

    assert ideas == []


def test_prompt_formatting_includes_snapshot_and_time() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)
    snapshot = _make_snapshot(symbol="000660", price=123000.0)

    with patch(
        "agents.analyst_agent.litellm.completion",
        return_value=_mock_response({"ideas": []}),
    ) as mocked:
        agent.analyze([snapshot])

    messages = mocked.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "quantitative stock analyst" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "## Market Data" in messages[1]["content"]
    assert "000660" in messages[1]["content"]
    assert "## Current Date/Time" in messages[1]["content"]


def test_strategy_wrapper_delegates_to_agent() -> None:
    settings = load_settings("config")
    agent = AnalystAgent(settings)
    strategy = AnalystStrategy(agent)

    payload = {
        "ideas": [
            {
                "symbol": "005930",
                "side": "BUY",
                "confidence": 0.9,
                "horizon": "SWING",
                "thesis": "Delegation",
                "entry": 70000.0,
                "tp": 72100.0,
                "sl": 68600.0,
                "constraints": {},
            }
        ]
    }

    with patch(
        "agents.analyst_agent.litellm.completion", return_value=_mock_response(payload)
    ):
        ideas = strategy.generate([_make_snapshot()])

    assert len(ideas) == 1
    assert ideas[0].symbol == "005930"
