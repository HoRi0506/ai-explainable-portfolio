"""engine/reconciliation.py - 재조정 엔진 (Phase 1b).

브로커 상태(포지션, 현금, 미체결 주문, 체결)와 내부 상태(Portfolio, OMS)를 비교.
불일치 발견 시 거래를 동결(freeze)하여 안전을 보장.

설계 결정:
- 시작 시 + 주기적(기본 5분) 재조정.
- Position qty 불일치 -> CRITICAL -> 동결.
- Cash 불일치(허용 오차 초과) -> HIGH -> 동결.
- Open order ID 기반 불일치 -> HIGH -> 동결 (NEW 상태는 in-flight 허용).
- Fill check: configurable (disabled/warn/freeze). 기본 warn.
- Paper mode: 내부 무결성 검증(음수 현금/수량 체크).
- unfreeze()는 _is_reconciled=False로 리셋 -> 다음 루프에서 반드시 재조정.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from adapters.kis_adapter import KISAdapter
from engine.execution_oms import ExecutionOMS
from engine.logger import AuditLogger
from engine.portfolio import PortfolioManager
from schemas.models import AlertSeverity, Mismatch, Position, ReconciliationResult

module_logger = logging.getLogger(__name__)

_ORDER_MISMATCH_PREFIX = "open_order"
_ALLOWED_FILL_MODES = {"disabled", "warn", "freeze"}


class ReconciliationEngine:
    """브로커 상태와 내부 상태를 재조정하는 엔진."""

    def __init__(
        self,
        portfolio: PortfolioManager,
        oms: ExecutionOMS,
        broker: KISAdapter | None = None,
        logger: AuditLogger | None = None,
        cash_tolerance: float = 100.0,
        reconcile_interval_seconds: int = 300,
        fill_check_mode: str = "warn",
    ) -> None:
        """재조정 엔진을 초기화한다.

        Args:
            portfolio: 내부 포트폴리오 관리자.
            oms: 내부 OMS.
            broker: 브로커 어댑터. None이면 paper mode 무결성 검증만 수행.
            logger: 감사 로거.
            cash_tolerance: 현금 허용 오차 (KRW).
            reconcile_interval_seconds: 재조정 주기(초).
            fill_check_mode: 체결 확인 모드 (disabled | warn | freeze).
        """
        self._portfolio = portfolio
        self._oms = oms
        self._broker = broker
        self._logger = logger
        self._cash_tolerance = cash_tolerance
        self._reconcile_interval_seconds = reconcile_interval_seconds

        normalized_fill_mode = fill_check_mode.lower().strip()
        if normalized_fill_mode not in _ALLOWED_FILL_MODES:
            module_logger.warning(
                "Invalid fill_check_mode '%s'; fallback to 'warn'", fill_check_mode
            )
            normalized_fill_mode = "warn"
        self._fill_check_mode = normalized_fill_mode

        self._last_reconciled_at: datetime | None = None
        self._is_reconciled: bool = False
        self._frozen: bool = False

    def reconcile(self) -> ReconciliationResult:
        """브로커/내부 상태를 비교하여 재조정을 수행한다."""
        self._is_reconciled = False
        mismatches: list[Mismatch] = []
        broker_positions: list[Position] = []
        broker_cash = 0.0
        broker_orders: list[dict[str, Any]] = []

        portfolio = self._portfolio.get_portfolio()
        internal_orders = self._oms.get_open_orders()

        if self._broker is None:
            if portfolio.cash < 0:
                mismatches.append(
                    Mismatch(
                        field="paper.cash",
                        broker_value="N/A",
                        internal_value=str(portfolio.cash),
                        severity=AlertSeverity.CRITICAL,
                    )
                )

            for pos in portfolio.positions:
                if pos.qty <= 0:
                    mismatches.append(
                        Mismatch(
                            field=f"paper.position.{pos.symbol}",
                            broker_value="N/A",
                            internal_value=str(pos.qty),
                            severity=AlertSeverity.CRITICAL,
                        )
                    )

            for order in internal_orders:
                if order.status.value not in {"NEW", "SUBMITTED", "PARTIAL"}:
                    mismatches.append(
                        Mismatch(
                            field="paper.open_order.status",
                            broker_value="N/A",
                            internal_value=order.status.value,
                            severity=AlertSeverity.CRITICAL,
                        )
                    )

            if mismatches:
                self._frozen = True
                self._is_reconciled = False
                resolution = "Paper mode integrity violations detected. Trading frozen."
            else:
                self._frozen = False
                self._is_reconciled = True
                resolution = "Paper mode integrity check passed."

            self._last_reconciled_at = datetime.now(timezone.utc)
            result = ReconciliationResult(
                trace_id=uuid4(),
                timestamp=self._last_reconciled_at,
                broker_positions=[],
                broker_cash=0.0,
                broker_open_orders=0,
                internal_positions=portfolio.positions,
                internal_cash=portfolio.cash,
                mismatches=mismatches,
                resolution=resolution,
            )
            self._log_result(result)
            return result

        try:
            broker_balance_raw = self._broker.get_balance()
            broker_orders_raw = self._broker.get_order_status()
        except Exception:
            self._frozen = True
            self._is_reconciled = False
            raise

        if not isinstance(broker_balance_raw, dict):
            mismatches.append(
                Mismatch(
                    field="balance.invalid_payload",
                    broker_value=str(type(broker_balance_raw).__name__),
                    internal_value="dict",
                    severity=AlertSeverity.HIGH,
                )
            )
            self._frozen = True
            self._is_reconciled = False
            self._last_reconciled_at = datetime.now(timezone.utc)
            result = ReconciliationResult(
                trace_id=uuid4(),
                timestamp=self._last_reconciled_at,
                broker_positions=[],
                broker_cash=0.0,
                broker_open_orders=0,
                internal_positions=portfolio.positions,
                internal_cash=portfolio.cash,
                mismatches=mismatches,
                resolution="Invalid broker balance payload. Trading frozen.",
            )
            self._log_result(result)
            return result

        if not isinstance(broker_orders_raw, list):
            mismatches.append(
                Mismatch(
                    field="orders.invalid_payload",
                    broker_value=str(type(broker_orders_raw).__name__),
                    internal_value="list",
                    severity=AlertSeverity.HIGH,
                )
            )
            self._frozen = True
            self._is_reconciled = False
            self._last_reconciled_at = datetime.now(timezone.utc)
            result = ReconciliationResult(
                trace_id=uuid4(),
                timestamp=self._last_reconciled_at,
                broker_positions=[],
                broker_cash=0.0,
                broker_open_orders=0,
                internal_positions=portfolio.positions,
                internal_cash=portfolio.cash,
                mismatches=mismatches,
                resolution="Invalid broker orders payload. Trading frozen.",
            )
            self._log_result(result)
            return result

        broker_balance = broker_balance_raw
        broker_orders = broker_orders_raw

        broker_positions_raw_any = broker_balance.get("positions", [])
        broker_positions_raw = (
            broker_positions_raw_any
            if isinstance(broker_positions_raw_any, list)
            else []
        )
        broker_cash_missing = False
        broker_cash_raw = broker_balance.get("cash")
        if broker_cash_raw is None or not isinstance(broker_cash_raw, (int, float)):
            broker_cash_missing = True
            mismatches.append(
                Mismatch(
                    field="cash.missing",
                    broker_value=(
                        "MISSING" if broker_cash_raw is None else str(broker_cash_raw)
                    ),
                    internal_value=str(portfolio.cash),
                    severity=AlertSeverity.HIGH,
                )
            )
            broker_cash = 0.0
        else:
            broker_cash = float(broker_cash_raw)
        broker_positions = [
            Position(
                symbol=str(item.get("symbol", "")).strip(),
                qty=int(item.get("qty", 0) or 0),
                avg_price=float(item.get("avg_price", 0.0) or 0.0),
                current_price=0.0,
                unrealized_pnl=float(item.get("unrealized_pnl", 0.0) or 0.0),
                realized_pnl=0.0,
            )
            for item in broker_positions_raw
            if isinstance(item, dict) and str(item.get("symbol", "")).strip()
        ]

        broker_qty_map = {
            str(item.get("symbol", "")).strip(): int(item.get("qty", 0) or 0)
            for item in broker_positions_raw
            if isinstance(item, dict) and str(item.get("symbol", "")).strip()
        }
        internal_qty_map = {
            pos.symbol.strip(): int(pos.qty) for pos in portfolio.positions
        }
        for symbol in sorted(set(broker_qty_map) | set(internal_qty_map)):
            broker_qty = broker_qty_map.get(symbol)
            internal_qty = internal_qty_map.get(symbol)

            if broker_qty is None:
                mismatches.append(
                    Mismatch(
                        field=f"position.{symbol}",
                        broker_value="MISSING",
                        internal_value=str(internal_qty),
                        severity=AlertSeverity.CRITICAL,
                    )
                )
                continue
            if internal_qty is None:
                mismatches.append(
                    Mismatch(
                        field=f"position.{symbol}",
                        broker_value=str(broker_qty),
                        internal_value="MISSING",
                        severity=AlertSeverity.CRITICAL,
                    )
                )
                continue
            if broker_qty != internal_qty:
                mismatches.append(
                    Mismatch(
                        field=f"position.{symbol}",
                        broker_value=str(broker_qty),
                        internal_value=str(internal_qty),
                        severity=AlertSeverity.CRITICAL,
                    )
                )

        if not broker_cash_missing:
            broker_cash_int = int(round(broker_cash))
            internal_cash_int = int(round(portfolio.cash))
            if abs(broker_cash_int - internal_cash_int) > self._cash_tolerance:
                mismatches.append(
                    Mismatch(
                        field="cash",
                        broker_value=str(broker_cash_int),
                        internal_value=str(internal_cash_int),
                        severity=AlertSeverity.HIGH,
                    )
                )

        order_mismatches = self._compare_open_orders(
            broker_orders=broker_orders,
            internal_orders=internal_orders,
        )
        mismatches.extend(order_mismatches)

        if self._fill_check_mode != "disabled":
            try:
                fills = self._broker.get_fills()
                module_logger.info("Reconciliation fill check count=%d", len(fills))
            except Exception as exc:
                if self._fill_check_mode == "warn":
                    # 왜(why): warn 모드에서는 모든 fill API 에러를 경고로 처리한다.
                    # APTR0058 등 KIS 특수 에러도 동일하게 warn -> Phase 2에서 세분화 예정.
                    module_logger.warning("Fill check failed in warn mode: %s", exc)
                else:
                    mismatches.append(
                        Mismatch(
                            field="fills.fetch",
                            broker_value="ERROR",
                            internal_value=str(exc),
                            severity=AlertSeverity.HIGH,
                        )
                    )

        has_order_mismatch = any(self._is_order_mismatch(item) for item in mismatches)
        only_order_mismatch = bool(mismatches) and all(
            self._is_order_mismatch(item) for item in mismatches
        )
        if has_order_mismatch and only_order_mismatch:
            # 왜(why): ACK/FILL in-flight 전파 지연으로 인한 일시적 불일치를 1회 완화한다.
            time.sleep(2)
            internal_orders = self._oms.get_open_orders()
            refreshed_broker_orders = self._broker.get_order_status()
            refreshed_order_mismatches = self._compare_open_orders(
                broker_orders=refreshed_broker_orders,
                internal_orders=internal_orders,
            )
            other_mismatches = [m for m in mismatches if not self._is_order_mismatch(m)]
            mismatches = other_mismatches + refreshed_order_mismatches
            broker_orders = refreshed_broker_orders

        has_freeze = any(
            item.severity in {AlertSeverity.CRITICAL, AlertSeverity.HIGH}
            for item in mismatches
        )
        if has_freeze:
            self._frozen = True
            self._is_reconciled = False
            resolution = (
                "Reconciliation found high severity mismatches. Trading frozen."
            )
        else:
            self._frozen = False
            self._is_reconciled = True
            resolution = "Reconciliation passed."

        self._last_reconciled_at = datetime.now(timezone.utc)
        result = ReconciliationResult(
            trace_id=uuid4(),
            timestamp=self._last_reconciled_at,
            broker_positions=broker_positions,
            broker_cash=broker_cash,
            broker_open_orders=len(broker_orders),
            internal_positions=portfolio.positions,
            internal_cash=portfolio.cash,
            mismatches=mismatches,
            resolution=resolution,
        )
        self._log_result(result)
        return result

    def is_frozen(self) -> bool:
        """현재 재조정 엔진이 동결 상태인지 반환한다."""
        return self._frozen

    def is_reconciled(self) -> bool:
        """최근 재조정이 성공적으로 완료되었는지 반환한다."""
        return self._is_reconciled

    def needs_reconciliation(self) -> bool:
        """재조정 수행 필요 여부를 반환한다."""
        if not self._is_reconciled:
            return True
        if self._last_reconciled_at is None:
            return True
        elapsed = (
            datetime.now(timezone.utc) - self._last_reconciled_at
        ).total_seconds()
        return elapsed > self._reconcile_interval_seconds

    def check_trading_allowed(self) -> bool:
        """거래 허용 여부를 반환한다."""
        return self._is_reconciled and not self._frozen

    def unfreeze(self) -> None:
        """수동으로 동결을 해제하고 재조정 필요 상태로 되돌린다."""
        self._frozen = False
        self._is_reconciled = False

    def _log_result(self, result: ReconciliationResult) -> None:
        """재조정 결과를 감사 로그에 기록한다."""
        if self._logger is None:
            return
        try:
            self._logger.log(result, event_type="ReconciliationResult")
        except Exception as exc:
            module_logger.warning("Failed to write reconciliation audit log: %s", exc)

    def _compare_open_orders(
        self, broker_orders: list[dict[str, Any]], internal_orders: list[Any]
    ) -> list[Mismatch]:
        broker_map: dict[str, dict[str, Any]] = {}
        for order in broker_orders:
            if not isinstance(order, dict):
                continue
            odno = order.get("odno")
            if odno is None or str(odno).strip() == "":
                continue
            broker_map[str(odno).strip()] = order

        internal_map: dict[str, Any] = {}
        for order in internal_orders:
            broker_id = getattr(order, "broker_order_id", None) or ""
            broker_id_str = str(broker_id).strip()
            if not broker_id_str:
                continue
            internal_map[broker_id_str] = order

        mismatches: list[Mismatch] = []
        for broker_id in sorted(set(broker_map) - set(internal_map)):
            mismatches.append(
                Mismatch(
                    field=f"{_ORDER_MISMATCH_PREFIX}.unknown_broker.{broker_id}",
                    broker_value=broker_id,
                    internal_value="MISSING",
                    severity=AlertSeverity.HIGH,
                )
            )

        for internal_id in sorted(set(internal_map) - set(broker_map)):
            mismatches.append(
                Mismatch(
                    field=f"{_ORDER_MISMATCH_PREFIX}.unknown_internal.{internal_id}",
                    broker_value="MISSING",
                    internal_value=internal_id,
                    severity=AlertSeverity.HIGH,
                )
            )

        return mismatches

    @staticmethod
    def _is_order_mismatch(mismatch: Mismatch) -> bool:
        return mismatch.field.startswith(_ORDER_MISMATCH_PREFIX)
