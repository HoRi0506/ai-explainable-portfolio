"""
tests/test_market_calendar.py - MarketCalendar 종합 테스트.

테스트 범위:
- _normalize_date() 헬퍼 함수
- MarketCalendar 클래스 (KRX, NYSE)
- 거래일/비거래일 판정
- 개장/폐장 시각 조회
- 거래시간 내 판정
- 다음/이전 거래일 조회
- 캐싱 메커니즘
- DST 전환 처리
"""

import datetime as dt
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from engine.market_calendar import MarketCalendar, get_market_calendar, _normalize_date
from schemas.models import Venue


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(autouse=True)
def clear_cache():
    """각 테스트 전에 lru_cache 초기화."""
    get_market_calendar.cache_clear()
    yield


@pytest.fixture
def krx_calendar():
    """KRX 캘린더 인스턴스."""
    return MarketCalendar(Venue.KR)


@pytest.fixture
def nyse_calendar():
    """NYSE 캘린더 인스턴스."""
    return MarketCalendar(Venue.US)


# ============================================================================
# TestNormalizeDate
# ============================================================================


class TestNormalizeDate:
    """_normalize_date() 헬퍼 함수 테스트."""

    def test_normalize_from_string(self):
        """문자열 "2026-03-02" → 자정 tz-naive pd.Timestamp."""
        result = _normalize_date("2026-03-02")
        assert isinstance(result, pd.Timestamp)
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.tzinfo is None

    def test_normalize_from_date_object(self):
        """date 객체 → 자정 tz-naive pd.Timestamp."""
        d = date(2026, 3, 2)
        result = _normalize_date(d)
        assert isinstance(result, pd.Timestamp)
        assert result.hour == 0
        assert result.minute == 0
        assert result.tzinfo is None

    def test_normalize_from_datetime_with_time(self):
        """datetime 문자열 "2026-03-02 14:30" → 자정으로 정규화."""
        result = _normalize_date("2026-03-02 14:30")
        assert result.hour == 0
        assert result.minute == 0
        assert result.tzinfo is None

    def test_normalize_strips_timezone(self):
        """tz-aware 입력 → tz-naive 자정으로 변환."""
        dt_aware = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
        result = _normalize_date(dt_aware)
        assert result.hour == 0
        assert result.minute == 0
        assert result.tzinfo is None


# ============================================================================
# TestMarketCalendarKRX
# ============================================================================


class TestMarketCalendarKRX:
    """KRX 캘린더 테스트."""

    def test_weekday_is_trading_day(self, krx_calendar):
        """평일 금요일(2026-02-27)은 거래일."""
        assert krx_calendar.is_trading_day("2026-02-27") is True

    def test_weekend_not_trading_day(self, krx_calendar):
        """일요일(2026-03-01)은 거래일 아님."""
        assert krx_calendar.is_trading_day("2026-03-01") is False

    def test_new_years_day_not_trading_day(self, krx_calendar):
        """신정(2026-01-01)은 거래일 아님."""
        assert krx_calendar.is_trading_day("2026-01-01") is False

    def test_session_open_close_normal_day(self, krx_calendar):
        """정상 거래일(2026-02-27)의 개장/폐장 시각.

        KRX 정상 시간: 09:00 ~ 15:30 KST
        UTC로는 00:00 ~ 06:30 (KST = UTC+9)
        """
        open_dt, close_dt = krx_calendar.session_open_close("2026-02-27")

        # UTC-aware datetime 확인
        assert open_dt.tzinfo is not None
        assert close_dt.tzinfo is not None

        # KST로 변환하여 시간 확인
        open_kst = open_dt.astimezone(ZoneInfo("Asia/Seoul"))
        close_kst = close_dt.astimezone(ZoneInfo("Asia/Seoul"))

        assert open_kst.hour == 9
        assert open_kst.minute == 0
        assert close_kst.hour == 15
        assert close_kst.minute == 30

    def test_session_open_close_returns_utc_aware(self, krx_calendar):
        """session_open_close()는 UTC-aware datetime 반환."""
        open_dt, close_dt = krx_calendar.session_open_close("2026-02-27")
        # UTC timezone-aware 확인 (ZoneInfo('UTC') 또는 timezone.utc)
        assert open_dt.tzinfo is not None
        assert close_dt.tzinfo is not None
        # UTC 오프셋 확인
        assert open_dt.utcoffset() == dt.timedelta(0)
        assert close_dt.utcoffset() == dt.timedelta(0)

    def test_session_open_close_non_trading_day_raises(self, krx_calendar):
        """비거래일에 session_open_close() 호출 → ValueError."""
        with pytest.raises(ValueError, match="거래일이 아닙니다"):
            krx_calendar.session_open_close("2026-03-01")

    def test_is_within_trading_hours_during_session(self, krx_calendar):
        """거래시간 내 시각(2026-02-27 11:00 KST = 02:00 UTC) → True."""
        # 2026-02-27 11:00 KST = 2026-02-27 02:00 UTC
        dt_utc = datetime(2026, 2, 27, 2, 0, tzinfo=timezone.utc)
        assert krx_calendar.is_within_trading_hours(dt_utc) is True

    def test_is_within_trading_hours_outside_session(self, krx_calendar):
        """거래시간 외 시각(2026-02-27 17:00 KST = 08:00 UTC) → False."""
        # 2026-02-27 17:00 KST = 2026-02-27 08:00 UTC
        dt_utc = datetime(2026, 2, 27, 8, 0, tzinfo=timezone.utc)
        assert krx_calendar.is_within_trading_hours(dt_utc) is False

    def test_is_within_trading_hours_weekend(self, krx_calendar):
        """주말 어느 시각이든 → False."""
        # 2026-03-01 (일요일) 12:00 UTC
        dt_utc = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        assert krx_calendar.is_within_trading_hours(dt_utc) is False

    def test_is_within_trading_hours_naive_raises(self, krx_calendar):
        """timezone-naive datetime → ValueError."""
        dt_naive = datetime(2026, 3, 2, 11, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            krx_calendar.is_within_trading_hours(dt_naive)

    def test_is_within_trading_hours_non_utc_tz(self, krx_calendar):
        """KST datetime(2026-02-27 11:00 KST) 전달 → 올바르게 처리."""
        # 2026-02-27 11:00 KST
        dt_kst = datetime(2026, 2, 27, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        assert krx_calendar.is_within_trading_hours(dt_kst) is True

    def test_next_trading_day_from_friday(self, krx_calendar):
        """금요일(2026-02-27)의 다음 거래일 → 화요일(2026-03-03)."""
        result = krx_calendar.next_trading_day("2026-02-27")
        assert result == date(2026, 3, 3)

    def test_next_trading_day_from_weekend(self, krx_calendar):
        """토요일(2026-02-28)의 다음 거래일 → 화요일(2026-03-03)."""
        result = krx_calendar.next_trading_day("2026-02-28")
        assert result == date(2026, 3, 3)

    def test_previous_trading_day_from_monday(self, krx_calendar):
        """화요일(2026-03-03)의 이전 거래일 → 금요일(2026-02-27)."""
        result = krx_calendar.previous_trading_day("2026-03-03")
        assert result == date(2026, 2, 27)

    def test_previous_trading_day_from_weekend(self, krx_calendar):
        """일요일(2026-03-01)의 이전 거래일 → 금요일(2026-02-27)."""
        result = krx_calendar.previous_trading_day("2026-03-01")
        assert result == date(2026, 2, 27)

    def test_venue_property(self, krx_calendar):
        """venue 프로퍼티 → Venue.KR."""
        assert krx_calendar.venue == Venue.KR

    def test_tz_property(self, krx_calendar):
        """tz 프로퍼티 → Asia/Seoul."""
        assert str(krx_calendar.tz) == "Asia/Seoul"


# ============================================================================
# TestMarketCalendarNYSE
# ============================================================================


class TestMarketCalendarNYSE:
    """NYSE 캘린더 테스트."""

    def test_weekday_is_trading_day(self, nyse_calendar):
        """평일 월요일(2026-03-02)은 거래일."""
        assert nyse_calendar.is_trading_day("2026-03-02") is True

    def test_weekend_not_trading_day(self, nyse_calendar):
        """일요일(2026-03-01)은 거래일 아님."""
        assert nyse_calendar.is_trading_day("2026-03-01") is False

    def test_session_times_normal(self, nyse_calendar):
        """정상 거래일(2026-03-02)의 개장/폐장 시각.

        NYSE 정상 시간: 09:30 ~ 16:00 ET
        """
        open_dt, close_dt = nyse_calendar.session_open_close("2026-03-02")

        # UTC-aware datetime 확인
        assert open_dt.tzinfo is not None
        assert close_dt.tzinfo is not None

        # ET로 변환하여 시간 확인
        open_et = open_dt.astimezone(ZoneInfo("America/New_York"))
        close_et = close_dt.astimezone(ZoneInfo("America/New_York"))

        assert open_et.hour == 9
        assert open_et.minute == 30
        assert close_et.hour == 16
        assert close_et.minute == 0

    def test_dst_transition_spring(self, nyse_calendar):
        """봄 DST 전환(2026-03-09 월요일) 후 거래시간.

        2026-03-08 (일요일) 02:00 EST → 03:00 EDT로 변경.
        2026-03-09 (월요일)은 EDT 기준.
        """
        open_dt, close_dt = nyse_calendar.session_open_close("2026-03-09")

        # EDT로 변환하여 시간 확인 (여전히 09:30 ~ 16:00)
        open_et = open_dt.astimezone(ZoneInfo("America/New_York"))
        close_et = close_dt.astimezone(ZoneInfo("America/New_York"))

        assert open_et.hour == 9
        assert open_et.minute == 30
        assert close_et.hour == 16
        assert close_et.minute == 0

    def test_dst_transition_fall(self, nyse_calendar):
        """가을 DST 전환(2026-11-02 월요일) 후 거래시간.

        2026-11-01 (일요일) 02:00 EDT → 01:00 EST로 변경.
        2026-11-02 (월요일)은 EST 기준.
        """
        open_dt, close_dt = nyse_calendar.session_open_close("2026-11-02")

        # EST로 변환하여 시간 확인 (여전히 09:30 ~ 16:00)
        open_et = open_dt.astimezone(ZoneInfo("America/New_York"))
        close_et = close_dt.astimezone(ZoneInfo("America/New_York"))

        assert open_et.hour == 9
        assert open_et.minute == 30
        assert close_et.hour == 16
        assert close_et.minute == 0

    def test_venue_property(self, nyse_calendar):
        """venue 프로퍼티 → Venue.US."""
        assert nyse_calendar.venue == Venue.US

    def test_tz_property(self, nyse_calendar):
        """tz 프로퍼티 → America/New_York."""
        assert str(nyse_calendar.tz) == "America/New_York"


# ============================================================================
# TestGetMarketCalendar
# ============================================================================


class TestGetMarketCalendar:
    """get_market_calendar() 팩토리 함수 테스트."""

    def test_factory_returns_correct_venue(self):
        """get_market_calendar(Venue.KR).venue == Venue.KR."""
        cal = get_market_calendar(Venue.KR)
        assert cal.venue == Venue.KR

    def test_factory_caches_instances(self):
        """동일 venue에 대해 인스턴스 재사용 (캐싱)."""
        cal1 = get_market_calendar(Venue.KR)
        cal2 = get_market_calendar(Venue.KR)
        assert cal1 is cal2

    def test_factory_different_venues(self):
        """서로 다른 venue는 다른 인스턴스."""
        cal_kr = get_market_calendar(Venue.KR)
        cal_us = get_market_calendar(Venue.US)
        assert cal_kr is not cal_us
        assert cal_kr.venue == Venue.KR
        assert cal_us.venue == Venue.US
