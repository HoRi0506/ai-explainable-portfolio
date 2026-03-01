"""
engine/portfolio.py - 포트폴리오 관리 모듈 (Phase 1a).

인메모리 포지션 추적, PnL 계산(수수료 반영), MDD 추적.
수수료 포함 평균 매입가(fee-inclusive cost basis) 방식 채택.

설계 결정:
- avg_price는 매수 수수료를 포함한 실질 단가. 매도 시 realized PnL이 정확.
- MDD는 fill/가격 업데이트 시 모두 갱신.
- 단일 프로세스 전용 (thread-safety 미보장).
"""

from datetime import datetime, timezone

from schemas.models import Portfolio, Position, Side


class PortfolioManager:
    """포트폴리오 관리자.

    인메모리 포지션 추적, 수수료 포함 평균 매입가 기반 PnL 계산.
    """

    def __init__(self, initial_cash: float, fee_rate: float = 0.00015) -> None:
        """초기화.

        Args:
            initial_cash: 초기 현금 (KRW).
            fee_rate: 수수료율 (기본 0.015% = KIS 모의투자 기본).
                매수: (체결가 * 수량 + 수수료)가 실질 비용.
                매도: (체결가 * 수량 - 수수료)가 실질 수익.
        """
        self._initial_cash: float = initial_cash
        self._cash: float = initial_cash
        self._fee_rate: float = fee_rate
        self._positions: dict[str, Position] = {}
        self._daily_realized_pnl: float = 0.0
        self._peak_value: float = initial_cash
        self._total_fees: float = 0.0

    def apply_fill(
        self, symbol: str, side: Side, qty: int, price: float, fee: float | None = None
    ) -> Position | None:
        """체결 적용.

        BUY: 포지션 신규/추가. avg_price는 수수료 포함 실질 단가.
        SELL: 포지션 감소/제거. realized_pnl = (sell_price - avg_price) * qty - sell_fee.

        Args:
            symbol: 종목코드 (예: "005930").
            side: 매매 방향 (BUY/SELL).
            qty: 체결 수량 (양의 정수).
            price: 체결가 (양수).
            fee: 수수료 (0 이상). None이면 자동 계산.

        Returns:
            업데이트된 Position. 전량 매도 시 None.

        Raises:
            ValueError: 잘못된 입력 (qty<=0, price<=0, fee<0, 현금 부족, 수량 부족).
        """
        # 입력 검증
        if qty <= 0:
            raise ValueError(f"수량은 양의 정수여야 합니다: {qty}")
        if price <= 0:
            raise ValueError(f"가격은 양수여야 합니다: {price}")
        if fee is None:
            fee = qty * price * self._fee_rate
        if fee < 0:
            raise ValueError(f"수수료는 0 이상이어야 합니다: {fee}")

        if side == Side.BUY:
            return self._apply_buy(symbol, qty, price, fee)
        elif side == Side.SELL:
            return self._apply_sell(symbol, qty, price, fee)
        else:
            raise ValueError(f"알 수 없는 매매 방향: {side}")

    def _apply_buy(self, symbol: str, qty: int, price: float, fee: float) -> Position:
        """매수 체결 처리.

        avg_price = (기존총비용 + 신규총비용) / 총수량
        여기서 총비용 = 체결가 * 수량 + 수수료 (fee-inclusive).
        """
        cost = qty * price + fee
        if cost > self._cash:
            raise ValueError(f"현금 부족: 필요 {cost:.0f}, 보유 {self._cash:.0f}")

        self._cash -= cost
        self._total_fees += fee

        if symbol in self._positions:
            pos = self._positions[symbol]
            # 가중평균: (기존 avg * 기존 qty + 신규 price * 신규 qty + fee) / 총 qty
            total_qty = pos.qty + qty
            new_avg = (pos.avg_price * pos.qty + price * qty + fee) / total_qty
            # 현재가 기반 unrealized 재계산
            current = pos.current_price if pos.current_price > 0 else new_avg
            unrealized = (current - new_avg) * total_qty
            self._positions[symbol] = pos.model_copy(
                update={
                    "qty": total_qty,
                    "avg_price": new_avg,
                    "unrealized_pnl": unrealized,
                }
            )
        else:
            # 신규 포지션: avg_price = (price * qty + fee) / qty
            avg_with_fee = (price * qty + fee) / qty
            self._positions[symbol] = Position(
                symbol=symbol,
                qty=qty,
                avg_price=avg_with_fee,
                current_price=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
            )

        self._update_peak()
        return self._positions[symbol]

    def _apply_sell(
        self, symbol: str, qty: int, price: float, fee: float
    ) -> Position | None:
        """매도 체결 처리.

        realized_pnl = (sell_price - avg_price) * qty - sell_fee
        avg_price가 이미 buy_fee를 포함하므로 양쪽 수수료 모두 반영됨.
        """
        if symbol not in self._positions:
            raise ValueError(f"보유하지 않은 종목: {symbol}")
        pos = self._positions[symbol]
        if qty > pos.qty:
            raise ValueError(f"보유 수량 부족: 보유 {pos.qty}, 매도 {qty}")

        proceeds = qty * price - fee
        self._cash += proceeds
        self._total_fees += fee

        # realized PnL: avg_price가 fee-inclusive이므로 buy 수수료도 자동 반영
        realized = (price - pos.avg_price) * qty - fee
        self._daily_realized_pnl += realized

        new_qty = pos.qty - qty
        if new_qty == 0:
            # 전량 매도 → 포지션 제거
            del self._positions[symbol]
            self._update_peak()
            return None
        else:
            # 부분 매도 → qty 감소, realized 누적
            current = pos.current_price if pos.current_price > 0 else pos.avg_price
            unrealized = (current - pos.avg_price) * new_qty
            self._positions[symbol] = pos.model_copy(
                update={
                    "qty": new_qty,
                    "realized_pnl": pos.realized_pnl + realized,
                    "unrealized_pnl": unrealized,
                }
            )
            self._update_peak()
            return self._positions[symbol]

    def update_prices(self, prices: dict[str, float]) -> None:
        """현재가 업데이트 → 미실현 PnL 재계산.

        Args:
            prices: {종목코드: 현재가} 딕셔너리.
        """
        for symbol, current_price in prices.items():
            if symbol in self._positions:
                pos = self._positions[symbol]
                unrealized = (current_price - pos.avg_price) * pos.qty
                self._positions[symbol] = pos.model_copy(
                    update={
                        "current_price": current_price,
                        "unrealized_pnl": unrealized,
                    }
                )
        self._update_peak()

    def get_portfolio(self) -> Portfolio:
        """현재 포트폴리오 스냅샷 반환.

        Returns:
            Portfolio 객체 (Pydantic 모델).
        """
        total_value = self._calculate_total_value()
        mdd = 0.0
        if self._peak_value > 0:
            mdd = (self._peak_value - total_value) / self._peak_value * 100
            mdd = max(mdd, 0.0)  # 신고점이면 0
        return Portfolio(
            positions=list(self._positions.values()),
            cash=self._cash,
            total_value=total_value,
            daily_pnl=self._daily_realized_pnl,
            mdd=mdd,
            updated_at=datetime.now(timezone.utc),
        )

    def get_position(self, symbol: str) -> Position | None:
        """특정 종목 포지션 조회. 미보유 시 None."""
        return self._positions.get(symbol)

    def reset_daily_pnl(self) -> None:
        """일일 손익 초기화. 장 시작 시 호출.

        _daily_realized_pnl만 리셋. Position.realized_pnl (누적)은 유지.
        """
        self._daily_realized_pnl = 0.0

    def _calculate_total_value(self) -> float:
        """총 자산 = 현금 + 포지션 시가 합계.

        current_price가 0인 포지션은 avg_price로 대체 (아직 시세 미수신).
        """
        position_value = sum(
            (pos.current_price if pos.current_price > 0 else pos.avg_price) * pos.qty
            for pos in self._positions.values()
        )
        return self._cash + position_value

    def _update_peak(self) -> None:
        """총 자산이 신고점이면 _peak_value 갱신."""
        total = self._calculate_total_value()
        if total > self._peak_value:
            self._peak_value = total
