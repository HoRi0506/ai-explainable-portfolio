"""
tests/test_portfolio.py - PortfolioManager 종합 테스트.

22+ 테스트 케이스:
- 초기화 (2)
- 매수 (7)
- 매도 (6)
- 왕복 거래 (3)
- 현재가 업데이트 (3)
- MDD (4)
- 일일 손익 리셋 (2)
- 다중 포지션 (2)
"""

from datetime import datetime

import pytest

from engine.portfolio import PortfolioManager
from schemas.models import Portfolio, Side


@pytest.fixture
def pm() -> PortfolioManager:
    """10,000,000 KRW 초기 자금, 0.015% 수수료."""
    return PortfolioManager(initial_cash=10_000_000, fee_rate=0.00015)


class TestPortfolioManagerInit:
    """초기화 테스트."""

    def test_initial_state(self, pm: PortfolioManager) -> None:
        """초기 상태 검증: 현금, 포지션, 총 자산."""
        portfolio = pm.get_portfolio()
        assert portfolio.cash == 10_000_000
        assert len(portfolio.positions) == 0
        assert portfolio.total_value == 10_000_000
        assert portfolio.daily_pnl == 0.0
        assert portfolio.mdd == 0.0

    def test_initial_portfolio_snapshot(self, pm: PortfolioManager) -> None:
        """초기 포트폴리오 스냅샷이 유효한 Portfolio 객체인지 검증."""
        portfolio = pm.get_portfolio()
        assert isinstance(portfolio, Portfolio)
        assert isinstance(portfolio.updated_at, datetime)
        assert portfolio.updated_at.tzinfo is not None  # UTC timezone-aware


class TestBuy:
    """매수 테스트."""

    def test_buy_creates_position(self, pm: PortfolioManager) -> None:
        """매수로 포지션 신규 생성."""
        pos = pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        assert pos is not None
        assert pos.symbol == "005930"
        assert pos.qty == 10
        # 현금 = 10,000,000 - (10 * 70,000 + 1,050) = 10,000,000 - 701,050
        portfolio = pm.get_portfolio()
        assert portfolio.cash == 10_000_000 - 701_050

    def test_buy_fee_inclusive_avg_price(self, pm: PortfolioManager) -> None:
        """매수 수수료 포함 평균 매입가 검증."""
        pos = pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        assert pos is not None
        # avg_price = (70000 * 10 + 1050) / 10 = 70105.0
        assert pos.avg_price == pytest.approx(70105.0)

    def test_buy_auto_fee(self, pm: PortfolioManager) -> None:
        """수수료 자동 계산 (fee=None)."""
        pos = pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=None)
        assert pos is not None
        # auto fee = 10 * 70000 * 0.00015 = 105.0
        expected_fee = 10 * 70000 * 0.00015
        expected_avg = (70000 * 10 + expected_fee) / 10
        assert pos.avg_price == pytest.approx(expected_avg)

    def test_buy_additional_averaging(self, pm: PortfolioManager) -> None:
        """추가 매수로 평균 매입가 재계산."""
        # 1차 매수: 10주 @ 70000, fee=1050
        pos1 = pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        assert pos1 is not None
        avg1 = pos1.avg_price  # 70105.0

        # 2차 매수: 5주 @ 72000, fee=540
        pos2 = pm.apply_fill("005930", Side.BUY, 5, 72000.0, fee=540.0)
        assert pos2 is not None
        # new_avg = (70105 * 10 + 72000 * 5 + 540) / 15
        expected_avg = (avg1 * 10 + 72000 * 5 + 540) / 15
        assert pos2.avg_price == pytest.approx(expected_avg)
        assert pos2.qty == 15

    def test_buy_insufficient_cash(self, pm: PortfolioManager) -> None:
        """현금 부족 시 ValueError."""
        with pytest.raises(ValueError, match="현금 부족"):
            pm.apply_fill("005930", Side.BUY, 200, 70000.0, fee=2100.0)

    def test_buy_zero_qty_raises(self, pm: PortfolioManager) -> None:
        """수량 0 시 ValueError."""
        with pytest.raises(ValueError, match="수량은 양의 정수여야 합니다"):
            pm.apply_fill("005930", Side.BUY, 0, 70000.0, fee=0.0)

    def test_buy_negative_price_raises(self, pm: PortfolioManager) -> None:
        """음수 가격 시 ValueError."""
        with pytest.raises(ValueError, match="가격은 양수여야 합니다"):
            pm.apply_fill("005930", Side.BUY, 10, -70000.0, fee=0.0)

    def test_buy_negative_fee_raises(self, pm: PortfolioManager) -> None:
        """음수 수수료 시 ValueError."""
        with pytest.raises(ValueError, match="수수료는 0 이상이어야 합니다"):
            pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=-10.0)


class TestSell:
    """매도 테스트."""

    def test_sell_full_position_returns_none(self, pm: PortfolioManager) -> None:
        """전량 매도 시 None 반환, 포지션 제거."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        result = pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)
        assert result is None
        assert pm.get_position("005930") is None

    def test_sell_partial(self, pm: PortfolioManager) -> None:
        """부분 매도 시 포지션 수량 감소."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pos = pm.apply_fill("005930", Side.SELL, 5, 72000.0, fee=540.0)
        assert pos is not None
        assert pos.qty == 5
        assert pm.get_position("005930") is not None

    def test_sell_realized_pnl_with_fees(self, pm: PortfolioManager) -> None:
        """매도 실현 손익 (수수료 포함) 검증."""
        # BUY: 10주 @ 70000, fee=1050 → avg_price=70105
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        # SELL: 10주 @ 72000, fee=1080
        # realized = (72000 - 70105) * 10 - 1080 = 1895 * 10 - 1080 = 18950 - 1080 = 17870
        pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)
        portfolio = pm.get_portfolio()
        assert portfolio.daily_pnl == pytest.approx(17870.0)

    def test_sell_insufficient_qty(self, pm: PortfolioManager) -> None:
        """보유 수량 부족 시 ValueError."""
        pm.apply_fill("005930", Side.BUY, 5, 70000.0, fee=525.0)
        with pytest.raises(ValueError, match="보유 수량 부족"):
            pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)

    def test_sell_unknown_symbol(self, pm: PortfolioManager) -> None:
        """미보유 종목 매도 시 ValueError."""
        with pytest.raises(ValueError, match="보유하지 않은 종목"):
            pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)

    def test_sell_cash_increase(self, pm: PortfolioManager) -> None:
        """매도로 현금 증가 검증."""
        initial_cash = 10_000_000
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        cash_after_buy = pm.get_portfolio().cash
        # 매도: 10주 @ 72000, fee=1080 → proceeds = 720000 - 1080 = 718920
        pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)
        cash_after_sell = pm.get_portfolio().cash
        assert cash_after_sell == pytest.approx(cash_after_buy + 718920)


class TestRoundTripPnL:
    """왕복 거래 (매수 후 매도) PnL 테스트."""

    def test_buy_sell_profit(self, pm: PortfolioManager) -> None:
        """수익 거래: 매수 후 고가 매도."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("005930", Side.SELL, 10, 75000.0, fee=1125.0)
        portfolio = pm.get_portfolio()
        # realized = (75000 - 70105) * 10 - 1125 = 4895 * 10 - 1125 = 48950 - 1125 = 47825
        assert portfolio.daily_pnl == pytest.approx(47825.0)
        assert portfolio.daily_pnl > 0

    def test_buy_sell_loss(self, pm: PortfolioManager) -> None:
        """손실 거래: 매수 후 저가 매도."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("005930", Side.SELL, 10, 65000.0, fee=975.0)
        portfolio = pm.get_portfolio()
        # realized = (65000 - 70105) * 10 - 975 = -5105 * 10 - 975 = -51050 - 975 = -52025
        assert portfolio.daily_pnl == pytest.approx(-52025.0)
        assert portfolio.daily_pnl < 0

    def test_cash_pnl_consistency(self, pm: PortfolioManager) -> None:
        """현금 변화 = 일일 손익 (부동소수점 오차 허용)."""
        initial_cash = 10_000_000
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)
        portfolio = pm.get_portfolio()
        cash_change = portfolio.cash - initial_cash
        # cash_change = daily_pnl (정확히 일치해야 함)
        assert cash_change == pytest.approx(portfolio.daily_pnl, abs=1.0)


class TestUpdatePrices:
    """현재가 업데이트 테스트."""

    def test_update_prices_unrealized_pnl(self, pm: PortfolioManager) -> None:
        """현재가 업데이트로 미실현 손익 재계산."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        # avg_price = 70105
        pm.update_prices({"005930": 75000.0})
        pos = pm.get_position("005930")
        assert pos is not None
        # unrealized = (75000 - 70105) * 10 = 4895 * 10 = 48950
        assert pos.unrealized_pnl == pytest.approx(48950.0)
        assert pos.current_price == 75000.0

    def test_update_prices_unknown_symbol_ignored(self, pm: PortfolioManager) -> None:
        """미보유 종목 현재가 업데이트는 무시."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        # 미보유 종목 업데이트 → 에러 없음
        pm.update_prices({"999999": 50000.0})
        pos = pm.get_position("005930")
        assert pos is not None
        assert pos.current_price == 0.0  # 변경 없음

    def test_unrealized_after_fill(self, pm: PortfolioManager) -> None:
        """체결 후 미실현 손익 초기값 검증."""
        pos = pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        assert pos is not None
        # 체결 직후 current_price=0 → unrealized=0
        assert pos.unrealized_pnl == 0.0
        assert pos.current_price == 0.0


class TestMDD:
    """최대 낙폭 (MDD) 테스트."""

    def test_mdd_initial_zero(self, pm: PortfolioManager) -> None:
        """초기 MDD = 0."""
        portfolio = pm.get_portfolio()
        assert portfolio.mdd == 0.0

    def test_mdd_after_price_drop(self, pm: PortfolioManager) -> None:
        """가격 하락 후 MDD > 0."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        # 신고점: 10,000,000 (초기 현금 = 포지션 가치)
        # avg_price = 70105, 현금 = 9,298,950
        pm.update_prices({"005930": 63000.0})
        # 현재 자산: 9,298,950 + 10*63000 = 9,928,950
        portfolio = pm.get_portfolio()
        # MDD = (10,000,000 - 9,928,950) / 10,000,000 * 100 = 0.7105%
        expected_mdd = (10_000_000 - 9_928_950) / 10_000_000 * 100
        assert portfolio.mdd == pytest.approx(expected_mdd, rel=0.01)
        assert portfolio.mdd > 0

    def test_mdd_peak_updates_on_fill(self, pm: PortfolioManager) -> None:
        """수익 거래로 신고점 갱신."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("005930", Side.SELL, 10, 75000.0, fee=1125.0)
        # 신고점: 10,000,000 + 47825 = 10,047,825
        pm.update_prices({})  # 강제 갱신
        portfolio = pm.get_portfolio()
        # 신고점이 갱신되었으므로 MDD = 0
        assert portfolio.mdd == pytest.approx(0.0)

    def test_mdd_peak_updates_on_price_rise(self, pm: PortfolioManager) -> None:
        """가격 상승으로 신고점 갱신 후 MDD 리셋."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.update_prices({"005930": 63000.0})
        # MDD > 0
        portfolio1 = pm.get_portfolio()
        assert portfolio1.mdd > 0

        # 가격 상승으로 신고점 초과
        pm.update_prices({"005930": 80000.0})
        portfolio2 = pm.get_portfolio()
        # 신고점 갱신 → MDD = 0
        assert portfolio2.mdd == pytest.approx(0.0)


class TestDailyReset:
    """일일 손익 리셋 테스트."""

    def test_reset_daily_pnl(self, pm: PortfolioManager) -> None:
        """일일 손익 리셋."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)
        portfolio1 = pm.get_portfolio()
        assert portfolio1.daily_pnl > 0

        pm.reset_daily_pnl()
        portfolio2 = pm.get_portfolio()
        assert portfolio2.daily_pnl == 0.0

    def test_reset_preserves_positions(self, pm: PortfolioManager) -> None:
        """리셋이 포지션과 누적 손익을 보존."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("005930", Side.SELL, 5, 72000.0, fee=540.0)
        pos_before = pm.get_position("005930")
        assert pos_before is not None
        realized_before = pos_before.realized_pnl

        pm.reset_daily_pnl()
        pos_after = pm.get_position("005930")
        assert pos_after is not None
        assert pos_after.qty == pos_before.qty
        assert pos_after.realized_pnl == realized_before  # 누적 손익 유지


class TestMultiplePositions:
    """다중 포지션 테스트."""

    def test_manage_two_symbols(self, pm: PortfolioManager) -> None:
        """두 종목 동시 보유."""
        pos1 = pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pos2 = pm.apply_fill("000660", Side.BUY, 20, 50000.0, fee=1500.0)
        assert pos1 is not None
        assert pos2 is not None

        portfolio = pm.get_portfolio()
        assert len(portfolio.positions) == 2
        assert pm.get_position("005930") is not None
        assert pm.get_position("000660") is not None

    def test_sell_one_keep_other(self, pm: PortfolioManager) -> None:
        """한 종목 전량 매도, 다른 종목 유지."""
        pm.apply_fill("005930", Side.BUY, 10, 70000.0, fee=1050.0)
        pm.apply_fill("000660", Side.BUY, 20, 50000.0, fee=1500.0)

        pm.apply_fill("005930", Side.SELL, 10, 72000.0, fee=1080.0)
        portfolio = pm.get_portfolio()
        assert len(portfolio.positions) == 1
        assert pm.get_position("005930") is None
        assert pm.get_position("000660") is not None
