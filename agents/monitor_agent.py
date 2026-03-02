"""
agents/monitor_agent.py - 감시 에이전트.

규칙 기반 포지션 감시: 급변 감지, 데이터 신선도, MDD 위반, 일일 손실 한도.
AI가 아닌 순수 규칙 기반. 이상 감지 시 Alert 발행 + halt 설정.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Callable
from uuid import uuid4

from config.settings import AppConfig
from engine.logger import AuditLogger
from schemas.events import Alert
from schemas.models import (
    AlertAction,
    AlertSeverity,
    MarketSnapshot,
    Portfolio,
    RiskPolicy,
)

logger = logging.getLogger(__name__)


class MonitorAgent:
    """규칙 기반 감시 에이전트.

    4가지 규칙으로 포지션 감시:
    1. 급변 감지 (price change)
    2. 데이터 신선도 (stale data)
    3. MDD 위반 (max drawdown)
    4. 일일 손실 한도 (daily loss limit)
    """

    def __init__(
        self,
        config: AppConfig,
        policy: RiskPolicy,
        portfolio_fn: Callable[[], Portfolio],
        logger: AuditLogger,
    ) -> None:
        """감시 에이전트 초기화.

        Args:
            config: 앱 설정 (급변 감지 임계값, 시간 창, 데이터 부실 기준).
            policy: 리스크 정책 (MDD 한도, 일일 손실 한도).
            portfolio_fn: 현재 포트폴리오를 반환하는 콜백.
            logger: 감사 로깅 인스턴스.
        """
        self._threshold_pct: float = config.monitor_price_change_threshold_pct
        self._window_minutes: int = config.monitor_price_change_window_minutes
        self._stale_minutes: int = config.monitor_stale_data_minutes
        self._policy: RiskPolicy = policy
        self._portfolio_fn: Callable[[], Portfolio] = portfolio_fn
        self._logger: AuditLogger = logger
        self._halted: bool = False
        # 왜(why): 종목별 가격 이력을 유지하여 시간 창 내 급변을 감지한다.
        self._price_history: dict[str, list[tuple[datetime, float]]] = defaultdict(list)

    def check(
        self, snapshots: list[MarketSnapshot], now: datetime | None = None
    ) -> list[Alert]:
        """모든 규칙을 실행하고 알림 목록을 반환한다.

        Args:
            snapshots: 현재 시장 스냅샷 목록.
            now: 현재 시각 (테스트용 주입). None이면 UTC now 사용.

        Returns:
            발생한 알림 목록. STOP 액션이 포함되면 halt 상태로 전환.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        portfolio = self._portfolio_fn()
        alerts: list[Alert] = []

        # 왜(why): 가격 델타를 먼저 계산한 후에 새 가격을 기록해야 이전 값 대비 비교가 정확하다.
        alerts.extend(self._check_price_changes(snapshots, now))
        alerts.extend(self._check_stale_data(portfolio, snapshots, now))
        alerts.extend(self._check_mdd(portfolio, now))
        alerts.extend(self._check_daily_loss(portfolio, now))

        for alert in alerts:
            self._log_alert(alert)
            if alert.action == AlertAction.STOP:
                self._halted = True

        return alerts

    def is_halted(self) -> bool:
        """halt 상태 여부 반환."""
        return self._halted

    def reset_halt(self) -> None:
        """halt 상태 해제."""
        self._halted = False

    # ------------------------------------------------------------------
    # Rule 1: 급변 감지
    # ------------------------------------------------------------------

    def _check_price_changes(
        self, snapshots: list[MarketSnapshot], now: datetime
    ) -> list[Alert]:
        """시간 창 내 가격 급변을 감지한다.

        Args:
            snapshots: 현재 시장 스냅샷 목록.
            now: 현재 시각.

        Returns:
            급변 알림 목록. 급락=CRITICAL/STOP, 급등=HIGH/HOLD.
        """
        # 왜(why): 무한 증가를 방지하기 위해 검사 전에 오래된 이력을 제거한다.
        self._prune_history(now)

        alerts: list[Alert] = []
        for snapshot in snapshots:
            history = self._price_history.get(snapshot.symbol)
            if not history:
                continue  # 첫 관측 → 비교 대상 없음, 거짓 알림 방지

            old_price = history[0][1]
            if old_price <= 0:
                continue  # 제로 가격 가드: 0으로 나누기 방지

            change_pct = abs((snapshot.price - old_price) / old_price) * 100

            if change_pct >= self._threshold_pct:
                if snapshot.price < old_price:
                    alerts.append(
                        Alert(
                            trace_id=uuid4(),
                            ts=now,
                            severity=AlertSeverity.CRITICAL,
                            message=f"{snapshot.symbol} 급락 {change_pct:.1f}% ({self._window_minutes}분)",
                            action=AlertAction.STOP,
                        )
                    )
                else:
                    alerts.append(
                        Alert(
                            trace_id=uuid4(),
                            ts=now,
                            severity=AlertSeverity.HIGH,
                            message=f"{snapshot.symbol} 급등 {change_pct:.1f}% ({self._window_minutes}분)",
                            action=AlertAction.HOLD,
                        )
                    )

        # 왜(why): 델타 계산이 끝난 후에 새 가격을 기록하여 다음 비교 기준을 업데이트한다.
        self._record_prices(snapshots)
        return alerts

    # ------------------------------------------------------------------
    # Rule 2: 데이터 신선도
    # ------------------------------------------------------------------

    def _check_stale_data(
        self,
        portfolio: Portfolio,
        snapshots: list[MarketSnapshot],
        now: datetime,
    ) -> list[Alert]:
        """보유 종목의 데이터 누락/부실을 감지한다.

        Args:
            portfolio: 현재 포트폴리오.
            snapshots: 현재 시장 스냅샷 목록.
            now: 현재 시각.

        Returns:
            데이터 누락/부실 알림 목록. HIGH/STOP.
        """
        snapshot_symbols = {s.symbol for s in snapshots}
        snapshot_ts = {s.symbol: s.ts for s in snapshots}
        alerts: list[Alert] = []

        for position in portfolio.positions:
            if position.qty <= 0:
                continue
            symbol = position.symbol

            if symbol not in snapshot_symbols:
                alerts.append(
                    Alert(
                        trace_id=uuid4(),
                        ts=now,
                        severity=AlertSeverity.HIGH,
                        message=f"{symbol} 데이터 누락 (보유 중)",
                        action=AlertAction.STOP,
                    )
                )
            else:
                age_seconds = (now - snapshot_ts[symbol]).total_seconds()
                # 왜(why): 포트폴리오가 최근 업데이트되면 새 포지션이 생성되었을 가능성이 높으므로 부실 데이터 체단을 내린다.
                portfolio_age_seconds = (now - portfolio.updated_at).total_seconds()
                if age_seconds > self._stale_minutes * 60 and portfolio_age_seconds > 300:
                    age_minutes = age_seconds / 60
                    alerts.append(
                        Alert(
                            trace_id=uuid4(),
                            ts=now,
                            severity=AlertSeverity.HIGH,
                            message=f"{symbol} 데이터 부실 ({age_minutes:.0f}분 경과)",
                            action=AlertAction.STOP,
                        )
                    )

        return alerts

    # ------------------------------------------------------------------
    # Rule 3: MDD 위반
    # ------------------------------------------------------------------

    def _check_mdd(self, portfolio: Portfolio, now: datetime) -> list[Alert]:
        """MDD가 한도를 초과했는지 검사한다.

        Args:
            portfolio: 현재 포트폴리오.
            now: 현재 시각.

        Returns:
            MDD 위반 알림 (0개 또는 1개). CRITICAL/STOP.
        """
        if portfolio.mdd >= self._policy.max_drawdown_pct:
            return [
                Alert(
                    trace_id=uuid4(),
                    ts=now,
                    severity=AlertSeverity.CRITICAL,
                    message=f"MDD {portfolio.mdd:.1f}% >= 한도 {self._policy.max_drawdown_pct:.1f}%",
                    action=AlertAction.STOP,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Rule 4: 일일 손실 한도
    # ------------------------------------------------------------------

    def _check_daily_loss(self, portfolio: Portfolio, now: datetime) -> list[Alert]:
        """일일 손실이 한도를 초과했는지 검사한다.

        Args:
            portfolio: 현재 포트폴리오.
            now: 현재 시각.

        Returns:
            일일 손실 위반 알림 (0개 또는 1개). CRITICAL/STOP.
        """
        if portfolio.daily_pnl >= 0:
            return []
        if portfolio.total_value <= 0:
            return []

        loss_pct = abs(portfolio.daily_pnl) / portfolio.total_value * 100
        if loss_pct >= self._policy.daily_loss_limit_pct:
            return [
                Alert(
                    trace_id=uuid4(),
                    ts=now,
                    severity=AlertSeverity.CRITICAL,
                    message=f"일일 손실 {loss_pct:.1f}% >= 한도 {self._policy.daily_loss_limit_pct:.1f}%",
                    action=AlertAction.STOP,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_prices(self, snapshots: list[MarketSnapshot]) -> None:
        """스냅샷의 가격을 이력에 기록한다.

        왜(why): snapshot.ts를 사용하여 실제 데이터 시각 기준으로 이력을 관리한다.
        """
        for snapshot in snapshots:
            self._price_history[snapshot.symbol].append((snapshot.ts, snapshot.price))

    def _prune_history(self, now: datetime) -> None:
        """시간 창을 초과한 오래된 가격 이력을 제거한다.

        Args:
            now: 현재 시각. 이 시각에서 window_minutes를 뺀 시점 이전 데이터 삭제.
        """
        cutoff = now - timedelta(minutes=self._window_minutes)
        for symbol in list(self._price_history.keys()):
            self._price_history[symbol] = [
                (ts, price) for ts, price in self._price_history[symbol] if ts >= cutoff
            ]

    def _log_alert(self, alert: Alert) -> None:
        """알림을 감사 로그에 기록하고 WARNING 레벨로 출력한다."""
        self._logger.log(alert, event_type="MonitorAlert")
        logger.warning("[Monitor] %s: %s", alert.severity.value, alert.message)
