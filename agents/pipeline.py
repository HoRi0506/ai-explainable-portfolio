"""agents/pipeline.py - 에이전트 파이프라인 (Phase 1a).

DataHub -> AnalystAgent/StrategyHub -> RiskGate -> OMS -> KISAdapter -> AuditLog.
run_once()는 단일 실행만 담당하며, run_loop()는 Phase 1b에서 추가한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from uuid import uuid4

import httpx
from pydantic import BaseModel

from adapters.kis_adapter import KISAPIError, KISAdapter
from agents.analyst_agent import AnalystAgent, AnalystStrategy
from agents.monitor_agent import MonitorAgent
from schemas.events import Alert
from schemas.models import AlertAction, ApprovedOrderPlan, OrderStatus, TradeIdea, TradingMode
from config.settings import Settings
from engine.circuit_breaker import CircuitBreaker, CircuitState
from engine.kill_switch import KillSwitch, KillSwitchLevel
from engine.data_hub import DataHub
from engine.execution_oms import ExecutionOMS
from engine.logger import AuditLogger
from engine.market_calendar import get_market_calendar
from engine.portfolio import PortfolioManager
from engine.reconciliation import ReconciliationEngine
from engine.risk_gate import RiskGate
from engine.strategy_hub import Strategy, StrategyHub


_MAX_FILL_POLL_ATTEMPTS = 5
_FILL_POLL_INTERVAL_SECONDS = 3
logger = logging.getLogger(__name__)


class _PipelineLogEvent(BaseModel):
    run_id: str
    timestamp: datetime
    snapshots_collected: int
    ideas_generated: int
    orders_approved: int
    orders_submitted: int
    orders_filled: int
    errors: list[str]


@dataclass
class PipelineResult:
    run_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snapshots_collected: int = 0
    ideas_generated: int = 0
    orders_approved: int = 0
    orders_submitted: int = 0
    orders_filled: int = 0
    errors: list[str] = field(default_factory=list)


class TradingPipeline:
    def __init__(
        self, settings: Settings, kis_adapter: KISAdapter | None = None
    ) -> None:
        self._settings: Settings = settings
        self._kis_adapter: KISAdapter | None = kis_adapter
        self._portfolio: PortfolioManager = PortfolioManager(
            initial_cash=10_000_000,
            fee_rate=0.00015,
        )

        log_path = Path(settings.app.log_path)
        log_dir = log_path.parent if log_path.suffix else log_path
        self._audit_logger: AuditLogger = AuditLogger(log_dir=log_dir)
        self._oms: ExecutionOMS = ExecutionOMS(
            db_path=Path(settings.app.db_path),
            portfolio=self._portfolio,
            logger=self._audit_logger,
        )
        self._risk_gate: RiskGate = RiskGate(
            max_data_age_seconds=settings.app.data_stale_minutes * 60
        )
        self._market_calendar = get_market_calendar(settings.app.venue)
        self._strategy_hub: StrategyHub = StrategyHub(settings.strategy)
        self._analyst_strategy: Strategy | None = None

        try:
            self._analyst_strategy = AnalystStrategy(AnalystAgent(settings))
            self._strategy_hub.register("analyst_agent", self._analyst_strategy)
        except Exception as exc:
            warning = PipelineResult(errors=[f"AnalystStrategy 등록 실패: {exc}"])
            self._audit_logger.log(
                _PipelineLogEvent.model_validate(asdict(warning)),
                event_type="PipelineWarning",
            )

        self._filled_qty_by_order: dict[str, int] = {}
        self._oms_recovery_failed: bool = False
        self._circuit_breaker: CircuitBreaker = CircuitBreaker(
            failure_threshold=5, cooldown_seconds=60.0
        )

        # 왜(why): 재시작 시 미확인 주문 상태 복구. 실패해도 재조정 게이트가 거래를 차단한다.
        try:
            self._oms.recover_open_orders()
        except Exception as exc:
            self._oms_recovery_failed = True
            logger.warning("recover_open_orders failed: %s", exc)
            self._audit_logger.log(
                _PipelineLogEvent.model_validate(
                    asdict(
                        PipelineResult(errors=[f"recover_open_orders failed: {exc}"])
                    )
                ),
                event_type="PipelineWarning",
            )

        self._reconciliation_engine: ReconciliationEngine = ReconciliationEngine(
            portfolio=self._portfolio,
            oms=self._oms,
            broker=self._kis_adapter,
            logger=self._audit_logger,
            cash_tolerance=settings.app.reconciliation_cash_tolerance,
            reconcile_interval_seconds=settings.app.reconciliation_interval_seconds,
            fill_check_mode=settings.app.reconciliation_fill_check_mode,
        )

        self._monitor: MonitorAgent = MonitorAgent(
            config=settings.app,
            policy=settings.risk.get_active(),
            portfolio_fn=self._portfolio.get_portfolio,
            logger=self._audit_logger,
        )

        self._kill_switch: KillSwitch = KillSwitch()

    def run_once(self, symbols: list[str] | None = None) -> PipelineResult:
        start = time.monotonic()
        deadline = start + self._settings.app.pipeline_timeout_sec
        result = PipelineResult()
        if self._kill_switch.is_active:
            result.errors.append(
                f"Kill Switch {self._kill_switch.level.value}: {self._kill_switch.reason}"
            )
            self._log_result(result)
            return result
        if self._monitor.is_halted():
            result.errors.append("Monitor HALTED - trading blocked")
            self._log_result(result)
            return result
        if self._oms_recovery_failed:
            result.errors.append("OMS recovery failed - trading blocked")
            self._log_result(result)
            return result

        mode = self._settings.app.trading_mode
        if mode == TradingMode.PAUSED:
            result.errors.append("PAUSED mode - pipeline skipped")
            self._log_result(result)
            return result

        now = datetime.now(timezone.utc)
        if not self._market_calendar.is_within_trading_hours(now):
            result.errors.append("Out of trading hours - pipeline skipped")
            self._log_result(result)
            return result

        # 왜(why): 재조정 완료 전 거래 금지 (crash-safe disarmed 원칙)
        if self._reconciliation_engine.needs_reconciliation():
            try:
                recon_result = self._reconciliation_engine.reconcile()
                if self._reconciliation_engine.is_frozen():
                    result.errors.append(
                        f"Reconciliation FROZEN: {recon_result.resolution}"
                    )
                    self._log_result(result)
                    return result
            except Exception as exc:
                result.errors.append(f"Reconciliation error: {exc}")
                self._log_result(result)
                return result

        if not self._reconciliation_engine.check_trading_allowed():
            result.errors.append("Trading not allowed: reconciliation required")
            self._log_result(result)
            return result

        try:
            data_hub = DataHub(
                symbols=symbols or [],
                venue=self._settings.app.venue,
                data_stale_minutes=self._settings.app.data_stale_minutes,
                max_collections_per_day=self._settings.app.max_collections_per_day,
            )
            snapshots = data_hub.collect()
            result.snapshots_collected = len(snapshots)
        except Exception as exc:
            result.errors.append(f"데이터 수집 실패: {exc}")
            logger.error("[%s] 데이터 수집 실패: %s", result.run_id[:8], exc)
            self._log_result(result)
            return result

        if self._check_timeout(start, result.run_id, result):
            self._log_result(result)
            return result

        if not snapshots:
            result.errors.append("No snapshots collected")
            self._log_result(result)
            return result

        ideas: list[TradeIdea] = []
        if self._analyst_strategy is not None:
            try:
                ideas = self._analyst_strategy.generate(snapshots)
            except Exception as exc:
                result.errors.append(f"Analyst strategy failed: {exc}")
        if not ideas:
            try:
                ideas = self._strategy_hub.generate(snapshots)
            except Exception as exc:
                result.errors.append(f"StrategyHub generation failed: {exc}")

        result.ideas_generated = len(ideas)
        if self._check_timeout(start, result.run_id, result):
            self._log_result(result)
            return result

        if not ideas:
            result.errors.append("No trade ideas generated")
            self._log_result(result)
            return result

        policy = self._settings.risk.get_active()
        data_asof = max(snapshot.ts for snapshot in snapshots)
        approved_plans: list[ApprovedOrderPlan] = []
        for idea in ideas:
            gate_result = self._risk_gate.evaluate(
                idea=idea,
                portfolio=self._portfolio.get_portfolio(),
                policy=policy,
                mode=mode,
                now=now,
                data_asof=data_asof,
            )
            if isinstance(gate_result, ApprovedOrderPlan):
                approved_plans.append(gate_result)
            else:
                result.errors.append(f"Rejected {idea.symbol}: {gate_result.reason}")

        result.orders_approved = len(approved_plans)
        for plan in approved_plans:
            order_id = ""
            try:
                order_result = self._oms.submit_order(plan)
                order_id = str(order_result.trace_id)
                idem_key = str(order_result.idempotency_key)
                result.orders_submitted += 1

                if mode == TradingMode.PAPER:
                    _ = self._oms.on_ack(order_id, f"PAPER-{order_id[:8]}")
                    continue

                if self._kis_adapter is None:
                    raise RuntimeError("REAL mode requires a configured KIS adapter")

                # 왜(why): 서킷이 OPEN이면 브로커 과부하를 방지하기 위해 배치 전체를 중단한다.
                allowed, wait_secs = self._circuit_breaker.before_request()
                if not allowed:
                    result.errors.append(
                        f"Circuit OPEN - batch halted. Order {order_id} deferred."
                    )
                    logger.warning(
                        "[%s] Circuit OPEN, halting batch. Wait %.1fs",
                        result.run_id[:8],
                        wait_secs,
                    )
                    break

                if wait_secs > 0:
                    time.sleep(wait_secs)

                broker_ack = self._kis_adapter.submit_order(
                    plan, client_order_id=idem_key
                )
                self._circuit_breaker.record_success()
                odno = str(broker_ack.get("odno", ""))
                if not odno:
                    raise RuntimeError("KIS submit_order returned empty odno")

                _ = self._oms.on_ack(order_id, odno)
                if self._check_timeout(start, result.run_id, result):
                    self._log_result(result)
                    return result
                if self._poll_fills(order_id, odno, deadline):
                    result.orders_filled += 1

            except KISAPIError as exc:
                # 왜(why): 결정론적 비즈니스 에러는 브로커 장애가 아니므로 서킷에 영향 없음.
                self._circuit_breaker.record_failure(transient=False)
                if order_id:
                    self._oms.on_reject(order_id, str(exc))
                result.errors.append(f"Order rejected {plan.order.symbol}: {exc}")
            except httpx.HTTPStatusError as exc:
                is_transient = exc.response.status_code in {429, 500, 502, 503, 504}
                self._circuit_breaker.record_failure(transient=is_transient)
                result.errors.append(f"HTTP error {plan.order.symbol}: {exc}")
                if is_transient and self._circuit_breaker.state == CircuitState.OPEN:
                    result.errors.append("Circuit opened - halting remaining orders")
                    break
            except (httpx.RequestError, TimeoutError) as exc:
                # 왜(why): 네트워크/타임아웃은 일시적 에러이므로 서킷 카운터를 증가시킨다.
                self._circuit_breaker.record_failure(transient=True)
                result.errors.append(f"Network error {plan.order.symbol}: {exc}")
                if self._circuit_breaker.state == CircuitState.OPEN:
                    result.errors.append("Circuit opened - halting remaining orders")
                    break
            except (RuntimeError, ValueError) as exc:
                result.errors.append(f"Order failed {plan.order.symbol}: {exc}")
            except Exception as exc:
                result.errors.append(
                    f"Unexpected order error {plan.order.symbol}: {exc}"
                )

        # 왜(why): 주문 실행 후 모니터 에이전트가 포트폴리오 상태를 점검하여 이상 감지
        try:
            snapshots_for_monitor = snapshots if snapshots else []
            alerts = self._monitor.check(snapshots_for_monitor)
            if alerts:
                for alert in alerts:
                    result.errors.append(f"[Monitor] {alert.severity.value}: {alert.message}")
                # 왜(why): STOP 알림 발생 시 킬 스위치를 활성화하여 다음 run_once부터 거래를 차단한다.
                stop_alerts = [a for a in alerts if a.action == AlertAction.STOP]
                if stop_alerts:
                    self._kill_switch.activate(
                        KillSwitchLevel.PAUSE,
                        reason=f"Monitor STOP: {stop_alerts[0].message}",
                    )
        except Exception as exc:
            result.errors.append(f"Monitor check failed: {exc}")

        self._log_result(result)
        return result

    def _check_timeout(self, start: float, run_id: str, result: PipelineResult) -> bool:
        """타임아웃 예산 초과 확인.

        Returns:
            True if timeout exceeded.
        """
        elapsed = time.monotonic() - start
        if elapsed >= self._settings.app.pipeline_timeout_sec:
            result.errors.append(
                f"파이프라인 타임아웃 ({elapsed:.1f}s >= {self._settings.app.pipeline_timeout_sec}s)"
            )
            logger.warning("[%s] 파이프라인 타임아웃: %.1fs", run_id[:8], elapsed)
            return True
        return False

    def _poll_fills(self, order_id: str, odno: str, deadline: float) -> bool:
        if self._kis_adapter is None:
            return False

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        processed_qty = self._filled_qty_by_order.get(order_id, 0)
        for _ in range(_MAX_FILL_POLL_ATTEMPTS):
            if time.monotonic() >= deadline:
                break
            fills = self._kis_adapter.get_fills(date_str=date_str)
            for fill in fills:
                if str(fill.get("odno", "")) != odno:
                    continue

                cumulative_qty = int(fill.get("ccld_qty", 0) or 0)
                if cumulative_qty <= processed_qty:
                    continue

                delta_qty = cumulative_qty - processed_qty
                price = float(fill.get("ccld_unpr", 0.0) or 0.0)
                if delta_qty <= 0 or price <= 0:
                    continue

                fill_time = self._parse_fill_time(str(fill.get("ccld_dttm", "")))
                fill_id = (
                    f"{odno}-{fill_time.strftime('%Y%m%d%H%M%S')}-{cumulative_qty}"
                )
                oms_result = self._oms.on_fill(
                    order_id=order_id,
                    fill_id=fill_id,
                    qty=delta_qty,
                    price=price,
                    fee=0.0,
                    filled_at=fill_time,
                )
                processed_qty = cumulative_qty
                self._filled_qty_by_order[order_id] = processed_qty
                if oms_result is not None and oms_result.status == OrderStatus.FILLED:
                    return True

            time.sleep(_FILL_POLL_INTERVAL_SECONDS)
        return False

    def close(self) -> None:
        self._oms.close()
        self._audit_logger.close()
        if self._kis_adapter is not None:
            self._kis_adapter.close()

    def _log_result(self, result: PipelineResult) -> None:
        self._audit_logger.log(
            _PipelineLogEvent.model_validate(asdict(result)),
            event_type="PipelineResult",
        )

    @staticmethod
    def _parse_fill_time(raw: str) -> datetime:
        if not raw:
            return datetime.now(timezone.utc)

        for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                parsed = datetime.strptime(raw, fmt)
                return (
                    parsed.replace(tzinfo=timezone.utc)
                    if parsed.tzinfo is None
                    else parsed.astimezone(timezone.utc)
                )
            except ValueError:
                continue

        try:
            parsed_iso = datetime.fromisoformat(raw)
            return (
                parsed_iso.replace(tzinfo=timezone.utc)
                if parsed_iso.tzinfo is None
                else parsed_iso.astimezone(timezone.utc)
            )
        except ValueError:
            return datetime.now(timezone.utc)
