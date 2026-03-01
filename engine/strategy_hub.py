"""
engine/strategy_hub.py - 전략 허브 (Phase 1a).

전략 인터페이스(Strategy ABC)와 기본 이동평균 크로스오버 전략 제공.
StrategyHub가 전략 레지스트리 역할을 하며, 활성 전략을 실행하여 TradeIdea를 생성.

설계 결정:
- Hub가 snapshots를 (venue, symbol) 그루핑 + ts 정렬 후 전략에 전달.
- 전략은 단일 심볼의 정렬된 시계열을 받는다.
- Phase 1a는 동기 시그니처. C-5 LLM 전략은 async wrapper로 감싸야 함.
- tp/sl tick 보정은 Risk Gate/OMS 책임 (여기서는 raw float).
"""

from abc import ABC, abstractmethod
from itertools import groupby

from config.settings import MAStrategy, StrategyConfig
from schemas.models import Horizon, MarketSnapshot, Side, TradeIdea


class Strategy(ABC):
    """전략 인터페이스.

    구현체는 단일 심볼의 시간순 정렬된 MarketSnapshot 리스트를 받아
    TradeIdea 리스트를 반환한다.

    Note:
        Phase 1a는 동기 시그니처. LLM 전략(C-5)은 async wrapper 필요.
    """

    @abstractmethod
    def generate(self, snapshots: list[MarketSnapshot]) -> list[TradeIdea]:
        """단일 심볼의 시계열 스냅샷에서 TradeIdea를 생성.

        Args:
            snapshots: 단일 심볼, ts 오름차순 정렬된 MarketSnapshot 리스트.
                       candle.close 사용, 없으면 price fallback.

        Returns:
            TradeIdea 리스트 (빈 리스트 가능).
        """


class MACrossoverStrategy(Strategy):
    """이동평균 크로스오버 전략.

    골든크로스(단기MA > 장기MA 교차) -> BUY
    데드크로스(단기MA < 장기MA 교차) -> SELL

    confidence = min(1.0, abs(short_ma - long_ma) / max(abs(long_ma), 1e-9))
    min_confidence 미달 시 필터링.
    """

    def __init__(self, params: MAStrategy) -> None:
        self._short = params.short_window
        self._long = params.long_window
        self._min_conf = params.min_confidence
        self._tp_pct = params.tp_pct
        self._sl_pct = params.sl_pct

    def generate(self, snapshots: list[MarketSnapshot]) -> list[TradeIdea]:
        """이동평균 교차 시그널을 계산해 TradeIdea를 생성한다.

        Args:
            snapshots: 단일 심볼, ts 오름차순 정렬된 MarketSnapshot 리스트.

        Returns:
            생성된 TradeIdea 리스트. 조건 미충족 시 빈 리스트.
        """
        prices: list[float] = []
        for snapshot in snapshots:
            price = snapshot.candle.close if snapshot.candle else snapshot.price
            if price > 0:
                prices.append(price)

        if len(prices) < self._long + 1:
            return []

        cur_short = sum(prices[-self._short :]) / self._short
        cur_long = sum(prices[-self._long :]) / self._long
        prev_short = sum(prices[-self._short - 1 : -1]) / self._short
        prev_long = sum(prices[-self._long - 1 : -1]) / self._long

        golden = prev_short <= prev_long and cur_short > cur_long
        dead = prev_short >= prev_long and cur_short < cur_long
        if not golden and not dead:
            return []

        side = Side.BUY if golden else Side.SELL
        confidence = min(1.0, abs(cur_short - cur_long) / max(abs(cur_long), 1e-9))
        if confidence < self._min_conf:
            return []

        entry = prices[-1]
        if side == Side.BUY:
            tp = entry * (1 + self._tp_pct)
            sl = entry * (1 - self._sl_pct)
        else:
            tp = entry * (1 - self._tp_pct)
            sl = entry * (1 + self._sl_pct)

        latest_snap = snapshots[-1]
        idea = TradeIdea(
            symbol=latest_snap.symbol,
            side=side,
            confidence=confidence,
            horizon=Horizon.SWING,
            thesis=f"MA crossover: short({self._short})={cur_short:.2f}, long({self._long})={cur_long:.2f}",
            entry=entry,
            tp=tp,
            sl=sl,
            constraints={
                "venue": latest_snap.venue.value,
                "data_asof": latest_snap.ts.isoformat(),
                "short_window": self._short,
                "long_window": self._long,
            },
        )
        return [idea]


class StrategyHub:
    """전략 레지스트리 + 라우터.

    여러 전략을 등록하고, 활성 전략을 실행하여 TradeIdea를 생성.
    입력 snapshots를 (venue, symbol)로 그루핑 + ts 정렬 후 전략에 전달.

    Args:
        config: StrategyConfig (strategy.yaml에서 로드)
    """

    def __init__(self, config: StrategyConfig) -> None:
        self._strategies: dict[str, Strategy] = {}
        self._active_name = config.active_strategy

        for name, params in config.strategies.items():
            if name == "ma_crossover":
                self._strategies[name] = MACrossoverStrategy(params)

        if self._active_name not in self._strategies:
            raise ValueError(
                f"Active strategy '{self._active_name}' not registered. "
                f"Available: {list(self._strategies.keys())}"
            )

    def register(self, name: str, strategy: Strategy) -> None:
        """전략 등록. (C-5 LLM 전략 연결 대비)

        Args:
            name: 전략 이름.
            strategy: Strategy 구현체.
        """
        self._strategies[name] = strategy

    @property
    def active_strategy_name(self) -> str:
        """현재 활성 전략 이름."""
        return self._active_name

    def generate(self, snapshots: list[MarketSnapshot]) -> list[TradeIdea]:
        """활성 전략으로 TradeIdea 생성.

        snapshots를 (venue, symbol)로 그루핑 + ts 오름차순 정렬 후
        각 그룹에 대해 활성 전략을 실행한다.

        Args:
            snapshots: 여러 심볼/거래소 혼합 가능한 MarketSnapshot 리스트.

        Returns:
            모든 심볼에서 생성된 TradeIdea 리스트.
        """
        strategy = self._strategies[self._active_name]
        sorted_snaps = sorted(snapshots, key=lambda s: (s.venue.value, s.symbol, s.ts))

        results: list[TradeIdea] = []
        for _key, group in groupby(
            sorted_snaps,
            key=lambda s: (s.venue.value, s.symbol),
        ):
            group_list = list(group)
            ideas = strategy.generate(group_list)
            results.extend(ideas)

        return results
