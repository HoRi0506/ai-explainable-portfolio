"""
engine/data_hub.py - 시세 수집 + 신선도 체크 (Phase 1a).

yfinance 래핑하여 MarketSnapshot[]을 생성한다.
수집 횟수 제한(하루 최대 N회) + 캐시(data_stale_minutes 이내 재사용).

설계 결정:
- 인메모리 캐시, 프로세스 재시작 시 초기화.
- KR 종목은 yfinance에서 '.KS' 접미사 필요 (예: '005930.KS').
- MarketSnapshot.symbol은 KIS 형식(6자리, 예: '005930')으로 저장.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pandas as pd

from schemas.models import Candle, MarketSnapshot, Venue
from tools import yfinance_client


class DataHub:
    """시장 데이터 수집 허브.

    yfinance를 통해 시세를 수집하고, 신선한 데이터는 캐시에서 재사용한다.
    """

    def __init__(
        self,
        symbols: list[str],
        venue: Venue,
        data_stale_minutes: int = 30,
        max_collections_per_day: int = 2,
    ) -> None:
        """초기화.

        Args:
            symbols: KIS 형식 종목코드 리스트 (예: ["005930", "000660"]).
            venue: 거래소 (KR/US).
            data_stale_minutes: 이 시간 이내면 캐시 사용.
            max_collections_per_day: 하루 최대 수집 횟수.
        """
        self._symbols = symbols
        self._venue = venue
        self._stale_delta = timedelta(minutes=data_stale_minutes)
        self._max_collections_per_day = max_collections_per_day

        self._cache: dict[str, tuple[MarketSnapshot, datetime]] = {}
        self._collection_date: date | None = None
        self._collection_count = 0

    def collect(self) -> list[MarketSnapshot]:
        """시장 데이터 수집.

        캐시가 신선하면 캐시를 반환하고, 그렇지 않으면 yfinance에서 재수집한다.

        Returns:
            종목별 MarketSnapshot 리스트.
            수집 제한 초과 또는 전체 실패 시 빈 리스트.
        """
        now = datetime.now(timezone.utc)
        self._reset_daily_counter_if_needed(now)

        if self._all_symbols_fresh(now):
            return [
                self._cache[symbol][0]
                for symbol in self._symbols
                if symbol in self._cache
            ]

        if self._collection_count >= self._max_collections_per_day:
            return []

        collected: list[MarketSnapshot] = []
        for symbol in self._symbols:
            cached = self._cache.get(symbol)
            if cached is not None and now - cached[1] < self._stale_delta:
                collected.append(cached[0])
                continue

            snapshot = self._fetch_snapshot(symbol)
            if snapshot is not None:
                self._cache[symbol] = (snapshot, now)
                collected.append(snapshot)

        self._collection_count += 1
        return collected

    def _fetch_snapshot(self, symbol: str) -> MarketSnapshot | None:
        """단일 종목 시세를 수집한다.

        Args:
            symbol: KIS 형식 종목코드.

        Returns:
            성공 시 MarketSnapshot, 실패 시 None.
        """
        ticker = self._to_yfinance_ticker(symbol)
        # 왜(why): yfinance 외부 호출은 네트워크/파싱/429 등으로 실패할 수 있으므로
        # 개별 종목 실패가 전체 수집을 중단하지 않도록 보호한다.
        try:
            info = yfinance_client.info(ticker)
        except Exception:
            return None

        price_raw = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("last_price")
        )
        if not isinstance(price_raw, (int, float, str)):
            return None
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            return None

        if price <= 0:
            return None

        bid = self._to_optional_float(info.get("bid"))
        ask = self._to_optional_float(info.get("ask"))
        volume = self._to_int(info.get("volume"))

        try:
            history_df = yfinance_client.history(ticker, period="5d")
        except Exception:
            history_df = None
        candle = self._latest_candle(history_df)

        return MarketSnapshot(
            trace_id=uuid4(),
            ts=datetime.now(timezone.utc),
            venue=self._venue,
            symbol=symbol,
            price=price,
            bid=bid,
            ask=ask,
            volume=volume,
            candle=candle,
            features=None,
        )

    def _to_yfinance_ticker(self, symbol: str) -> str:
        """KIS 종목코드를 yfinance 티커로 변환한다.

        Args:
            symbol: KIS 형식 종목코드.

        Returns:
            yfinance 티커 문자열.
        """
        if self._venue == Venue.KR:
            return f"{symbol}.KS"
        return symbol

    def _all_symbols_fresh(self, now: datetime) -> bool:
        """모든 종목 캐시가 신선한지 확인한다."""
        if not self._symbols:
            return False
        for symbol in self._symbols:
            cached = self._cache.get(symbol)
            if cached is None:
                return False
            if now - cached[1] >= self._stale_delta:
                return False
        return True

    def _reset_daily_counter_if_needed(self, now: datetime) -> None:
        """일일 수집 카운터를 날짜 기준으로 리셋한다."""
        today = now.date()
        if self._collection_date != today:
            self._collection_date = today
            self._collection_count = 0

    @staticmethod
    def _latest_candle(history_df: pd.DataFrame | None) -> Candle | None:
        """히스토리 데이터프레임에서 최신 캔들을 생성한다."""
        if history_df is None or history_df.empty:
            return None

        row = history_df.iloc[-1]
        try:
            return Candle(
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(float(row["Volume"])),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _to_optional_float(value: object) -> float | None:
        """None-safe float 변환."""
        if not isinstance(value, (int, float, str)):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: object) -> int:
        """None-safe int 변환. 실패 시 0 반환."""
        if not isinstance(value, (int, float, str)):
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
