"""
engine/market_calendar.py - exchange_calendars 래퍼 (Phase 1a).

KRX(한국거래소)/NYSE 거래일·거래시간 조회.
exchange_calendars 라이브러리를 감싸서 표준 Python datetime으로 변환.

주의:
- 모든 시각은 UTC timezone-aware datetime으로 반환.
- KRX 특수일(수능, 연초 첫 영업일)은 개장 시각이 다르므로 하드코딩 금지.
- 인스턴스 캐싱: get_market_calendar()로 재사용.
"""

from datetime import date, datetime, timezone, tzinfo
from functools import lru_cache
from typing import cast

import exchange_calendars as xcals
import pandas as pd

from schemas.models import Venue

# 거래소 코드 매핑
VENUE_MAP: dict[str, str] = {
    "KR": "XKRX",
    "US": "XNYS",
}


def _normalize_date(d: date | str) -> pd.Timestamp:
    """date/str 입력을 자정(midnight) tz-naive pd.Timestamp로 정규화.

    exchange_calendars의 세션 레이블은 tz-naive 자정 타임스탬프.
    "2026-03-02 12:34" 같은 입력도 "2026-03-02 00:00"으로 변환.

    Args:
        d: 날짜 (date, str "YYYY-MM-DD", 또는 datetime-like).

    Returns:
        tz-naive midnight pd.Timestamp.
    """
    ts = cast(pd.Timestamp, pd.Timestamp(str(d)))
    normalized = cast(pd.Timestamp, ts.normalize())
    if ts.tzinfo is not None:
        return cast(pd.Timestamp, normalized.tz_localize(None))
    return normalized


class MarketCalendar:
    """exchange_calendars 래퍼. 거래일/거래시간 조회.

    exchange_calendars가 반환하는 pd.Timestamp(UTC)를
    표준 Python datetime(UTC-aware)으로 변환하여 제공.
    """

    _cal: xcals.ExchangeCalendar  # type: ignore[name-defined]
    _venue: Venue

    def __init__(self, venue: Venue) -> None:
        """거래소 캘린더 초기화.

        Args:
            venue: 거래소 구분 (KR 또는 US).
        """
        self._cal = xcals.get_calendar(VENUE_MAP[venue.value])
        self._venue = venue

    @property
    def tz(self) -> tzinfo:
        """거래소 로컬 타임존.

        Returns:
            거래소의 타임존 (예: Asia/Seoul, America/New_York).
        """
        return self._cal.tz

    @property
    def venue(self) -> Venue:
        """거래소 구분."""
        return self._venue

    def is_trading_day(self, d: date | str) -> bool:
        """주어진 날짜가 거래일인지 확인.

        Args:
            d: 날짜 (date 객체 또는 "YYYY-MM-DD" 문자열).

        Returns:
            거래일이면 True, 주말/공휴일이면 False.
        """
        return self._cal.is_session(_normalize_date(d))

    def session_open_close(self, d: date | str) -> tuple[datetime, datetime]:
        """거래일의 개장/폐장 시각 반환 (UTC-aware datetime).

        KRX 특수일(수능, 연초 첫 영업일)의 시간 변동도 정확히 반영.

        Args:
            d: 거래일 날짜.

        Returns:
            (개장 시각, 폐장 시각) — 둘 다 UTC timezone-aware.

        Raises:
            ValueError: 거래일이 아닌 날짜를 지정한 경우.
        """
        ts = _normalize_date(d)
        if not self._cal.is_session(ts):
            raise ValueError(f"{d}은(는) 거래일이 아닙니다")
        open_ts, close_ts = self._cal.session_open_close(ts)
        return open_ts.to_pydatetime(), close_ts.to_pydatetime()

    def is_within_trading_hours(self, dt_input: datetime | None = None) -> bool:
        """현재(또는 지정) 시각이 거래시간 내인지 확인.

        Args:
            dt_input: 확인할 시각 (timezone-aware). None이면 현재 UTC 시각 사용.

        Returns:
            거래시간 내면 True.

        Raises:
            ValueError: timezone-naive datetime을 전달한 경우.
        """
        if dt_input is None:
            dt_input = datetime.now(timezone.utc)
        if dt_input.tzinfo is None:
            raise ValueError("timezone-aware datetime이 필요합니다")
        # 명시적 UTC 변환 후 pd.Timestamp으로 변환
        dt_utc = dt_input.astimezone(timezone.utc)
        ts = cast(pd.Timestamp, pd.Timestamp(dt_utc))
        return bool(self._cal.is_open_at_time(ts, side="left"))  # type: ignore[return-value]

    def next_trading_day(self, d: date | str) -> date:
        """다음 거래일 반환.

        d가 거래일이면 그 다음 거래일, 비거래일이면 가장 가까운 다음 거래일.

        Args:
            d: 기준 날짜.

        Returns:
            다음 거래일 (date 객체).
        """
        ts = _normalize_date(d)
        if self._cal.is_session(ts):
            return self._cal.next_session(ts).date()
        return self._cal.date_to_session(ts, direction="next").date()

    def previous_trading_day(self, d: date | str) -> date:
        """이전 거래일 반환.

        d가 거래일이면 그 직전 거래일, 비거래일이면 가장 가까운 이전 거래일.

        Args:
            d: 기준 날짜.

        Returns:
            이전 거래일 (date 객체).
        """
        ts = _normalize_date(d)
        if self._cal.is_session(ts):
            return self._cal.previous_session(ts).date()
        return self._cal.date_to_session(ts, direction="previous").date()


@lru_cache(maxsize=4)
def get_market_calendar(venue: Venue) -> MarketCalendar:
    """MarketCalendar 인스턴스 반환 (캐싱).

    동일 venue에 대해 인스턴스를 재사용하여 초기화 비용 절감.

    Args:
        venue: 거래소 구분.

    Returns:
        캐싱된 MarketCalendar 인스턴스.
    """
    return MarketCalendar(venue)
