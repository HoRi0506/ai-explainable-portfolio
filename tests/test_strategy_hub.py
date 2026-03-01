"""
tests/test_strategy_hub.py - StrategyHub 및 MA 전략 테스트.
"""

from datetime import datetime, timedelta, timezone

import pytest

from config.settings import MAStrategy, StrategyConfig
from engine.strategy_hub import MACrossoverStrategy, Strategy, StrategyHub
from schemas.models import Candle, Horizon, MarketSnapshot, Side, TradeIdea, Venue


def make_snapshots(
    symbol: str,
    prices: list[float],
    venue: Venue = Venue.KR,
    use_candle: bool = True,
    start_ts: datetime | None = None,
) -> list[MarketSnapshot]:
    """가격 시퀀스로 MarketSnapshot 리스트를 생성한다."""
    base_ts = start_ts or datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    snapshots: list[MarketSnapshot] = []
    for idx, price in enumerate(prices):
        candle = None
        if use_candle:
            candle = Candle(
                open=price,
                high=price,
                low=price,
                close=price,
                volume=1000,
            )
        snapshots.append(
            MarketSnapshot(
                ts=base_ts + timedelta(minutes=idx),
                venue=venue,
                symbol=symbol,
                price=price,
                volume=1000,
                candle=candle,
            )
        )
    return snapshots


class TestMACrossoverStrategy:
    """MACrossoverStrategy 테스트."""

    def test_golden_cross_generates_buy(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [200.0])

        ideas = strategy.generate(snapshots)

        assert len(ideas) == 1
        assert ideas[0].side == Side.BUY

    def test_dead_cross_generates_sell(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [1.0])

        ideas = strategy.generate(snapshots)

        assert len(ideas) == 1
        assert ideas[0].side == Side.SELL

    def test_no_cross_returns_empty(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 25)

        ideas = strategy.generate(snapshots)

        assert ideas == []

    def test_insufficient_data(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20)

        ideas = strategy.generate(snapshots)

        assert ideas == []

    def test_exactly_long_window_plus_one(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [200.0])

        ideas = strategy.generate(snapshots)

        assert len(ideas) == 1

    def test_low_confidence_filtered(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [101.0])

        ideas = strategy.generate(snapshots)

        assert ideas == []

    def test_candle_close_used(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        candle_prices = [100.0] * 20 + [200.0]
        snapshots: list[MarketSnapshot] = []
        base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        for idx, close_price in enumerate(candle_prices):
            snapshots.append(
                MarketSnapshot(
                    ts=base_ts + timedelta(minutes=idx),
                    venue=Venue.KR,
                    symbol="005930",
                    price=1.0,
                    volume=1000,
                    candle=Candle(
                        open=close_price,
                        high=close_price,
                        low=close_price,
                        close=close_price,
                        volume=1000,
                    ),
                )
            )

        ideas = strategy.generate(snapshots)

        assert len(ideas) == 1
        assert ideas[0].entry == 200.0

    def test_price_fallback_when_no_candle(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots(
            "005930",
            [100.0] * 20 + [200.0],
            use_candle=False,
        )

        ideas = strategy.generate(snapshots)

        assert len(ideas) == 1
        assert ideas[0].entry == 200.0

    def test_zero_price_skipped(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [0.0] + [100.0] * 20 + [200.0])

        ideas = strategy.generate(snapshots)

        assert len(ideas) == 1
        assert ideas[0].entry == 200.0

    def test_buy_tp_sl_direction(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [200.0])

        idea = strategy.generate(snapshots)[0]

        assert idea.side == Side.BUY
        assert idea.tp > idea.entry > idea.sl

    def test_sell_tp_sl_direction(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [1.0])

        idea = strategy.generate(snapshots)[0]

        assert idea.side == Side.SELL
        assert idea.sl > idea.entry > idea.tp

    def test_confidence_clamped_to_one(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.0))
        snapshots = make_snapshots("005930", [0.0001] * 20 + [100.0])

        idea = strategy.generate(snapshots)[0]

        assert idea.confidence == 1.0

    def test_thesis_contains_ma_values(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [200.0])

        idea = strategy.generate(snapshots)[0]

        assert "short(5)=" in idea.thesis
        assert "long(20)=" in idea.thesis

    def test_constraints_populated(self) -> None:
        strategy = MACrossoverStrategy(MAStrategy(min_confidence=0.01))
        snapshots = make_snapshots("005930", [100.0] * 20 + [200.0])

        idea = strategy.generate(snapshots)[0]

        assert idea.constraints is not None
        assert idea.constraints["venue"] == Venue.KR.value
        assert idea.constraints["data_asof"] == snapshots[-1].ts.isoformat()
        assert idea.constraints["short_window"] == 5
        assert idea.constraints["long_window"] == 20


class FixedStrategy(Strategy):
    """허브 라우팅 테스트용 고정 전략."""

    def generate(self, snapshots: list[MarketSnapshot]) -> list[TradeIdea]:
        if not snapshots:
            return []
        latest = snapshots[-1]
        return [
            TradeIdea(
                symbol=latest.symbol,
                side=Side.BUY,
                confidence=1.0,
                horizon=Horizon.SWING,
                thesis="fixed",
                entry=max(latest.price, 1.0),
                tp=max(latest.price, 1.0) * 1.01,
                sl=max(latest.price, 1.0) * 0.99,
            )
        ]


class TestStrategyHub:
    """StrategyHub 테스트."""

    def test_hub_with_valid_config(self) -> None:
        config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        )

        hub = StrategyHub(config)

        assert hub.active_strategy_name == "ma_crossover"

    def test_hub_missing_active_strategy(self) -> None:
        config = StrategyConfig(strategies={}, active_strategy="custom")

        with pytest.raises(ValueError, match="not registered"):
            StrategyHub(config)

    def test_hub_generate_groups_by_symbol(self) -> None:
        config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        )
        hub = StrategyHub(config)
        snaps = make_snapshots("005930", [100.0] * 20 + [200.0]) + make_snapshots(
            "000660", [100.0] * 20 + [1.0]
        )

        ideas = hub.generate(snaps)

        assert len(ideas) == 2
        assert {idea.symbol for idea in ideas} == {"005930", "000660"}

    def test_hub_generate_sorts_by_ts(self) -> None:
        config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        )
        hub = StrategyHub(config)
        snaps = make_snapshots("005930", [100.0] * 20 + [200.0])
        unsorted_snaps = list(reversed(snaps))

        ideas = hub.generate(unsorted_snaps)

        assert len(ideas) == 1
        assert ideas[0].entry == 200.0
        assert ideas[0].constraints is not None
        assert ideas[0].constraints["data_asof"] == snaps[-1].ts.isoformat()

    def test_hub_register_custom_strategy(self) -> None:
        config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        )
        hub = StrategyHub(config)
        hub.register("custom", FixedStrategy())
        hub._active_name = "custom"

        ideas = hub.generate(make_snapshots("005930", [100.0, 101.0, 102.0]))

        assert len(ideas) == 1
        assert ideas[0].thesis == "fixed"

    def test_hub_active_strategy_name(self) -> None:
        config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        )

        hub = StrategyHub(config)

        assert hub.active_strategy_name == "ma_crossover"

    def test_hub_empty_snapshots(self) -> None:
        config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy(min_confidence=0.01)},
            active_strategy="ma_crossover",
        )
        hub = StrategyHub(config)

        ideas = hub.generate([])

        assert ideas == []
