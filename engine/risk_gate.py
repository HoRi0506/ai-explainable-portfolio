"""
engine/risk_gate.py - 규칙 기반 리스크 관문 (Phase 1a).

TradeIdea를 8개 하드 규칙으로 점검하여 ApprovedOrderPlan 또는 Rejected 반환.
Phase 1a: config flag 기반. Phase 1b에서 HMAC capability token으로 강화 예정.

설계 결정:
- 모든 규칙을 전부 실행 (첫 실패에서 중단하지 않음) → 감사 로그 완전성.
- BUY: 8개 규칙 모두 적용. SELL: 모드/시간/주문건수만 적용 (리스크 감소 허용).
- SELL = 전량 매도 (Position.qty). 미보유 종목 SELL → 거부.
- sizing 후 qty ≤ 0이면 거부 (빈 주문 방지).
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from engine.capability_token import CapabilityTokenManager
from schemas.models import (
    ApprovedOrderPlan,
    BrokerOrder,
    OrderSizing,
    Portfolio,
    Rejected,
    RiskCheckResult,
    RiskPolicy,
    Side,
    TradingMode,
    TradeIdea,
)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_MAX_DATA_AGE_SECONDS = (
    1800  # 30분 (config/app.yaml의 data_stale_minutes와 동일)
)


class RiskGate:
    """규칙 기반 리스크 관문 (Phase 1a).

    8개 하드 규칙을 모두 실행하고 결과를 RiskCheckResult 리스트로 반환.
    하나라도 실패하면 Rejected, 전부 통과하면 ApprovedOrderPlan.
    """

    def __init__(
        self,
        max_data_age_seconds: int = DEFAULT_MAX_DATA_AGE_SECONDS,
        token_manager: CapabilityTokenManager | None = None,
    ) -> None:
        """초기화.

        Args:
            max_data_age_seconds: 데이터 최대 허용 나이 (초). 기본 1800초 (30분).
            token_manager: Phase 1b capability token 관리자. None이면 비활성화.
        """
        self._daily_order_count: int = 0
        self._last_reset_date: date | None = None
        self._max_data_age_seconds: int = max_data_age_seconds
        self._token_manager: CapabilityTokenManager | None = token_manager

    def evaluate(
        self,
        idea: TradeIdea,
        portfolio: Portfolio,
        policy: RiskPolicy,
        mode: TradingMode,
        now: datetime | None = None,
        data_asof: datetime | None = None,
    ) -> ApprovedOrderPlan | Rejected:
        """TradeIdea를 리스크 규칙으로 평가.

        Args:
            idea: 매매 아이디어.
            portfolio: 현재 포트폴리오 상태.
            policy: 리스크 정책.
            mode: 현재 자동매매 모드.
            now: 현재 시각 (UTC-aware). None이면 now(UTC).
            data_asof: 데이터 기준 시각 (UTC-aware). None이면 거부 (신선도 미확인).

        Returns:
            ApprovedOrderPlan (승인) 또는 Rejected (거부).
        """
        now = now or datetime.now(timezone.utc)
        self._maybe_reset_daily_count(now)

        checks: list[RiskCheckResult] = []

        # === 공통 규칙 (BUY + SELL 모두 적용) ===
        checks.append(self._check_trading_mode(mode))
        checks.append(self._check_market_hours(now, policy))
        checks.append(self._check_daily_orders(policy))
        checks.append(self._check_data_staleness(now, data_asof))

        # === BUY 전용 규칙 (SELL은 리스크 감소이므로 skip) ===
        if idea.side == Side.BUY:
            checks.append(self._check_daily_loss(portfolio, policy))
            checks.append(self._check_mdd(portfolio, policy))
            checks.append(self._check_concentration(idea, portfolio, policy))
            checks.append(self._check_max_positions(idea, portfolio, policy))

        # === SELL 전용: 보유 여부 확인 ===
        if idea.side == Side.SELL:
            checks.append(self._check_sell_has_position(idea, portfolio))

        # 실패 확인
        failed = [c for c in checks if not c.passed]
        if failed:
            return Rejected(
                trace_id=idea.trace_id,
                reason=failed[0].detail,
                risk_checks=checks,
            )

        # 사이징 계산
        sizing, order = self._build_order(idea, portfolio, policy)

        # qty ≤ 0이면 거부 (빈 주문 방지)
        if sizing.qty <= 0:
            checks.append(
                RiskCheckResult(
                    rule_name="sizing",
                    passed=False,
                    detail="주문 수량 계산 결과 0: 현금 부족 또는 비중 한도 초과",
                )
            )
            return Rejected(
                trace_id=idea.trace_id,
                reason="주문 수량 계산 결과 0",
                risk_checks=checks,
            )

        # 승인
        self._daily_order_count += 1
        capability_token = None
        if self._token_manager is not None:
            # 왜(why): token 서명 시 plan 필드가 최종 확정 상태여야 하므로,
            # token 없이 임시 plan을 만들어 서명한 뒤 최종 plan에 포함한다.
            unsigned_plan = ApprovedOrderPlan(
                trace_id=idea.trace_id,
                mode=mode,
                sizing=sizing,
                risk_checks=checks,
                order=order,
            )
            capability_token = self._token_manager.generate(unsigned_plan)
        return ApprovedOrderPlan(
            trace_id=idea.trace_id,
            mode=mode,
            sizing=sizing,
            risk_checks=checks,
            order=order,
            capability_token=capability_token,
        )

    def _check_trading_mode(self, mode: TradingMode) -> RiskCheckResult:
        """PAUSED 모드 차단."""
        passed = mode != TradingMode.PAUSED
        return RiskCheckResult(
            rule_name="trading_mode",
            passed=passed,
            detail="" if passed else "PAUSED 모드에서 모든 주문 차단",
        )

    def _check_market_hours(self, now: datetime, policy: RiskPolicy) -> RiskCheckResult:
        """거래 허용 시간 (KST) 확인."""
        kst = now.astimezone(KST)
        start_h, start_m = map(int, policy.trading_start.split(":"))
        end_h, end_m = map(int, policy.trading_end.split(":"))
        current_minutes = kst.hour * 60 + kst.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        passed = start_minutes <= current_minutes < end_minutes
        return RiskCheckResult(
            rule_name="market_hours",
            passed=passed,
            detail=""
            if passed
            else f"거래시간 외: 현재 {kst.strftime('%H:%M')} KST (허용: {policy.trading_start}~{policy.trading_end})",
        )

    def _check_daily_orders(self, policy: RiskPolicy) -> RiskCheckResult:
        """일일 주문 건수 확인."""
        passed = self._daily_order_count < policy.max_daily_orders
        return RiskCheckResult(
            rule_name="daily_orders",
            passed=passed,
            detail=""
            if passed
            else f"일일 주문 {self._daily_order_count} ≥ 한도 {policy.max_daily_orders}",
        )

    def _check_data_staleness(
        self, now: datetime, data_asof: datetime | None
    ) -> RiskCheckResult:
        """데이터 신선도 확인.

        data_asof가 None이면 거부 (신선도 미확인 = fail closed).
        """
        if data_asof is None:
            return RiskCheckResult(
                rule_name="data_staleness",
                passed=False,
                detail="데이터 기준 시각 미제공 (data_asof=None)",
            )
        age_seconds = (now - data_asof).total_seconds()
        passed = 0 <= age_seconds <= self._max_data_age_seconds
        return RiskCheckResult(
            rule_name="data_staleness",
            passed=passed,
            detail=""
            if passed
            else f"데이터 나이 {age_seconds:.0f}초 > 한도 {self._max_data_age_seconds}초",
        )

    def _check_daily_loss(
        self, portfolio: Portfolio, policy: RiskPolicy
    ) -> RiskCheckResult:
        """일일 손실 한도 확인 (BUY 전용).

        daily_pnl이 음수일 때 |daily_pnl| / total_value * 100 vs 한도.
        """
        if portfolio.total_value <= 0:
            return RiskCheckResult(
                rule_name="daily_loss", passed=False, detail="총 자산 0 이하"
            )
        loss_pct = abs(min(portfolio.daily_pnl, 0)) / portfolio.total_value * 100
        passed = loss_pct < policy.daily_loss_limit_pct
        return RiskCheckResult(
            rule_name="daily_loss",
            passed=passed,
            detail=""
            if passed
            else f"일일 손실 {loss_pct:.2f}% ≥ 한도 {policy.daily_loss_limit_pct}%",
        )

    def _check_mdd(self, portfolio: Portfolio, policy: RiskPolicy) -> RiskCheckResult:
        """MDD 한도 확인 (BUY 전용)."""
        passed = portfolio.mdd < policy.max_drawdown_pct
        return RiskCheckResult(
            rule_name="mdd",
            passed=passed,
            detail=""
            if passed
            else f"MDD {portfolio.mdd:.2f}% ≥ 한도 {policy.max_drawdown_pct}%",
        )

    def _check_concentration(
        self, idea: TradeIdea, portfolio: Portfolio, policy: RiskPolicy
    ) -> RiskCheckResult:
        """종목 집중도 확인 (BUY 전용).

        POST-TRADE weight 체크: (기존 포지션 가치 + 신규 매수 예상 가치) / 총 자산 * 100.
        신규 매수 가치 = 사이징 최대 가능 금액 (max_position_pct 기준의 나머지 여유분 포함).
        여기서는 기존 비중만 체크하고, 실제 사이징 시 한도를 넘지 않도록 _build_order에서 보장.
        """
        if portfolio.total_value <= 0:
            return RiskCheckResult(
                rule_name="concentration", passed=False, detail="총 자산 0 이하"
            )
        existing_value = sum(
            (p.current_price if p.current_price > 0 else p.avg_price) * p.qty
            for p in portfolio.positions
            if p.symbol == idea.symbol
        )
        existing_weight = existing_value / portfolio.total_value * 100
        # 기존 비중이 이미 한도 이상이면 추가 매수 불가
        passed = existing_weight < policy.max_position_pct
        return RiskCheckResult(
            rule_name="concentration",
            passed=passed,
            detail=""
            if passed
            else f"종목 {idea.symbol} 비중 {existing_weight:.1f}% ≥ 한도 {policy.max_position_pct}%",
        )

    def _check_max_positions(
        self, idea: TradeIdea, portfolio: Portfolio, policy: RiskPolicy
    ) -> RiskCheckResult:
        """최대 보유 종목 수 확인 (BUY 전용, 신규 종목만).

        이미 보유 중인 종목 추가 매수는 종목 수 제한에 걸리지 않음.
        """
        existing_symbols = {p.symbol for p in portfolio.positions}
        if idea.symbol in existing_symbols:
            return RiskCheckResult(
                rule_name="max_positions",
                passed=True,
                detail="기존 보유 종목 추가 매수",
            )
        passed = len(existing_symbols) < policy.max_positions
        return RiskCheckResult(
            rule_name="max_positions",
            passed=passed,
            detail=""
            if passed
            else f"보유 종목 수 {len(existing_symbols)} ≥ 한도 {policy.max_positions}",
        )

    def _check_sell_has_position(
        self, idea: TradeIdea, portfolio: Portfolio
    ) -> RiskCheckResult:
        """SELL 시 해당 종목 보유 여부 확인."""
        has_position = any(
            p.symbol == idea.symbol and p.qty > 0 for p in portfolio.positions
        )
        return RiskCheckResult(
            rule_name="sell_has_position",
            passed=has_position,
            detail="" if has_position else f"미보유 종목 매도 시도: {idea.symbol}",
        )

    def _build_order(
        self, idea: TradeIdea, portfolio: Portfolio, policy: RiskPolicy
    ) -> tuple[OrderSizing, BrokerOrder]:
        """주문 사이즈 계산 및 BrokerOrder 생성.

        BUY: max_position_pct 기준으로 최대 수량 계산 (기존 포지션 차감, 현금 한도).
        SELL: 보유 전량 매도.

        Returns:
            (OrderSizing, BrokerOrder) 튜플.
        """
        if idea.side == Side.SELL:
            return self._build_sell_order(idea, portfolio)
        return self._build_buy_order(idea, portfolio, policy)

    def _build_buy_order(
        self, idea: TradeIdea, portfolio: Portfolio, policy: RiskPolicy
    ) -> tuple[OrderSizing, BrokerOrder]:
        """BUY 주문 사이즈 계산."""
        max_notional = portfolio.total_value * policy.max_position_pct / 100
        existing_value = sum(
            (p.current_price if p.current_price > 0 else p.avg_price) * p.qty
            for p in portfolio.positions
            if p.symbol == idea.symbol
        )
        available = max(max_notional - existing_value, 0)
        available = min(available, portfolio.cash)
        qty = int(available // idea.entry) if idea.entry > 0 else 0
        qty = max(qty, 0)
        notional = qty * idea.entry
        weight = (
            notional / portfolio.total_value * 100 if portfolio.total_value > 0 else 0
        )
        sizing = OrderSizing(qty=qty, notional=notional, weight_pct=weight)
        order = BrokerOrder(
            symbol=idea.symbol, side=idea.side, qty=qty, price=idea.entry
        )
        return sizing, order

    def _build_sell_order(
        self, idea: TradeIdea, portfolio: Portfolio
    ) -> tuple[OrderSizing, BrokerOrder]:
        """SELL 주문: 보유 전량 매도."""
        pos = next((p for p in portfolio.positions if p.symbol == idea.symbol), None)
        qty = pos.qty if pos else 0
        price = (
            idea.entry
            if idea.entry > 0
            else (pos.current_price if pos and pos.current_price > 0 else 0)
        )
        notional = qty * price
        weight = (
            notional / portfolio.total_value * 100 if portfolio.total_value > 0 else 0
        )
        sizing = OrderSizing(qty=qty, notional=notional, weight_pct=weight)
        order = BrokerOrder(symbol=idea.symbol, side=idea.side, qty=qty, price=price)
        return sizing, order

    def reset_daily_count(self) -> None:
        """일일 주문 카운트 수동 리셋."""
        self._daily_order_count = 0
        self._last_reset_date = None

    def get_daily_order_count(self) -> int:
        """일일 주문 카운트 조회 (테스트용)."""
        return self._daily_order_count

    def set_daily_order_count(self, count: int) -> None:
        """일일 주문 카운트 설정 (테스트용)."""
        self._daily_order_count = count

    def set_last_reset_date(self, date_val: date | None) -> None:
        """마지막 리셋 날짜 설정 (테스트용)."""
        self._last_reset_date = date_val

    def _maybe_reset_daily_count(self, now: datetime) -> None:
        """날짜 변경 시 자동 리셋 (KST 기준)."""
        today = now.astimezone(KST).date()
        if self._last_reset_date != today:
            self._daily_order_count = 0
            self._last_reset_date = today
