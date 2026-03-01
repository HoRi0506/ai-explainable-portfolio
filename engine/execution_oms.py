"""
engine/execution_oms.py - 주문 관리 시스템 (Phase 1a).

ApprovedOrderPlan을 받아 브로커에 주문 전송 + 상태 추적.
SQLite WAL 모드로 주문/체결 영속화. 재시작 시 상태 복원.

설계 결정:
- order_id = str(plan.trace_id) - TradeIdea의 trace_id를 주문 ID로 사용.
- idempotency_key: 항상 생성 (uuid4). Phase 1a에서는 단순 저장, Phase 1b에서 브로커 활용.
- fills PK = (order_id, fill_id) - INSERT OR IGNORE로 중복 방지.
- on_fill: BEGIN IMMEDIATE -> INSERT fills -> UPDATE order -> COMMIT -> apply_fill -> log.
- 터미널 상태 (FILLED/CANCELED/REJECTED) 도달 후 추가 이벤트는 no-op.
- PRAGMA: journal_mode=WAL, synchronous=FULL, foreign_keys=ON, busy_timeout=5000.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from engine.logger import AuditLogger
from engine.portfolio import PortfolioManager
from schemas.models import ApprovedOrderPlan, Fill, OrderResult, OrderStatus

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {
        OrderStatus.SUBMITTED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELED,
    },
    OrderStatus.SUBMITTED: {
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIAL: {
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
    },
}

TERMINAL_STATES: set[OrderStatus] = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
}


class OrderEvent(BaseModel):
    """감사 로그용 주문 이벤트. capability_token 제외.

    Attributes:
        order_id: 내부 주문 ID (trace_id 문자열).
        event_type: 이벤트 타입 (submit, ack, fill, reject, cancel).
        status: 이벤트 처리 후 주문 상태.
        detail: 이벤트 상세 정보.
    """

    order_id: str
    event_type: str
    status: OrderStatus
    detail: dict[str, Any] = Field(default_factory=dict)


class ExecutionOMS:
    """SQLite 기반 동기식 OMS.

    주문 생성/상태 전이/체결 누적 계산을 단일 SQLite DB에 영속화한다.
    재시작 후 recover_open_orders로 미종결 주문을 복구할 수 있다.

    Notes:
        - Phase 1a는 단일 프로세스 기준이며 동기식으로 동작한다.
        - check_same_thread=False + 내부 lock으로 테스트의 동시 접근을 안전하게 직렬화한다.
        - 브로커 통신은 포함하지 않으며, 외부 ACK/FILL 이벤트를 메서드로 주입받는다.
    """

    _OPEN_STATUSES: tuple[str, str, str] = (
        OrderStatus.NEW.value,
        OrderStatus.SUBMITTED.value,
        OrderStatus.PARTIAL.value,
    )

    def __init__(
        self,
        db_path: str | Path,
        portfolio: PortfolioManager | None = None,
        logger: AuditLogger | None = None,
    ) -> None:
        """ExecutionOMS 초기화.

        Args:
            db_path: SQLite 파일 경로.
            portfolio: 체결 반영 대상 포트폴리오 관리자. None이면 포트폴리오 반영 생략.
            logger: 감사 로거. None이면 로그 기록 생략.
        """
        self._db_path = Path(db_path)
        self._portfolio = portfolio
        self._logger = logger
        self._lock = threading.RLock()

        self._conn = sqlite3.connect(
            self._db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_sqlite()
        self._create_tables()

    def submit_order(self, plan: ApprovedOrderPlan) -> OrderResult:
        """주문 등록.

        order_id는 trace_id 문자열을 사용한다. 동일 trace_id가 재요청되면
        기존 주문을 반환하여 멱등 동작을 제공한다.

        Args:
            plan: RiskGate 승인 주문 계획.

        Returns:
            생성(또는 기존) OrderResult. 상태는 NEW.
        """
        order_id = str(plan.trace_id)

        # 왜(why): Risk Gate가 이미 검증하지만, OMS 자체 방어로 비정상 주문 차단
        if plan.order.qty <= 0:
            raise ValueError(f"주문 수량은 양의 정수여야 합니다: {plan.order.qty}")
        if plan.order.price is not None and plan.order.price <= 0:
            raise ValueError(f"주문 가격은 양수여야 합니다: {plan.order.price}")

        with self._lock:
            existing = self._build_result(order_id)
            if existing is not None:
                return existing

            now_iso = self._utc_now_iso()
            idempotency_key = str(uuid4())

            self._conn.execute(
                """
                INSERT INTO orders (
                    order_id,
                    idempotency_key,
                    trace_id,
                    symbol,
                    side,
                    qty,
                    price,
                    order_type,
                    status,
                    filled_qty,
                    avg_fill_price,
                    total_fees,
                    broker_order_id,
                    created_at,
                    updated_at,
                    message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    idempotency_key,
                    order_id,
                    plan.order.symbol,
                    plan.order.side.value,
                    plan.order.qty,
                    plan.order.price,
                    plan.order.order_type,
                    OrderStatus.NEW.value,
                    0,
                    0.0,
                    0.0,
                    "",
                    now_iso,
                    now_iso,
                    "",
                ),
            )
            self._conn.commit()

            result = self._build_result(order_id)
            if result is None:
                raise RuntimeError(f"주문 생성 후 조회 실패: {order_id}")

        self._log_event(
            order_id=order_id,
            event_type="submit",
            status=result.status,
            detail={
                "symbol": plan.order.symbol,
                "side": plan.order.side.value,
                "qty": plan.order.qty,
                "price": plan.order.price,
                "order_type": plan.order.order_type,
            },
        )
        return result

    def on_ack(self, order_id: str, broker_order_id: str) -> OrderResult | None:
        """브로커 ACK 처리 (NEW -> SUBMITTED).

        Args:
            order_id: 내부 주문 ID.
            broker_order_id: 브로커 주문 ID.

        Returns:
            전이 성공 시 업데이트된 OrderResult, 실패 시 None.

        Raises:
            ValueError: order_id 또는 broker_order_id가 비어있으면 발생.
        """
        self._validate_non_empty(order_id, "order_id")
        self._validate_non_empty(broker_order_id, "broker_order_id")

        result = self._transition(
            order_id=order_id,
            new_status=OrderStatus.SUBMITTED,
            broker_order_id=broker_order_id,
        )

        if result is not None:
            self._log_event(
                order_id=order_id,
                event_type="ack",
                status=result.status,
                detail={"broker_order_id": broker_order_id},
            )
        return result

    def on_fill(
        self,
        order_id: str,
        fill_id: str,
        qty: int,
        price: float,
        fee: float = 0.0,
        filled_at: datetime | None = None,
    ) -> OrderResult | None:
        """체결 이벤트 반영.

        트랜잭션 순서:
            BEGIN IMMEDIATE -> INSERT OR IGNORE fills -> UPDATE orders -> COMMIT

        Args:
            order_id: 내부 주문 ID.
            fill_id: 체결 ID (order_id 내 유일).
            qty: 체결 수량 (양의 정수).
            price: 체결 가격 (양수).
            fee: 체결 수수료 (0 이상).
            filled_at: 체결 시각 (UTC-aware). None이면 현재 UTC.

        Returns:
            성공 시 업데이트된 OrderResult.
            중복 fill_id면 현재 상태를 반환.
            유효하지 않은 상태 전이면 None.

        Raises:
            ValueError: 입력 값이 유효하지 않으면 발생.
        """
        self._validate_non_empty(order_id, "order_id")
        self._validate_non_empty(fill_id, "fill_id")
        if qty <= 0:
            raise ValueError(f"수량은 양의 정수여야 합니다: {qty}")
        if price <= 0:
            raise ValueError(f"가격은 양수여야 합니다: {price}")
        if fee < 0:
            raise ValueError(f"수수료는 0 이상이어야 합니다: {fee}")

        fill_dt = self._ensure_utc_aware(filled_at or datetime.now(timezone.utc))

        with self._lock:
            order_row = self._get_order_row(order_id)
            if order_row is None:
                return None

            current_status = OrderStatus(order_row["status"])
            if current_status in TERMINAL_STATES:
                return None

            if current_status not in {OrderStatus.SUBMITTED, OrderStatus.PARTIAL}:
                return None

            self._conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO fills (
                        order_id, fill_id, qty, price, fee, filled_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        fill_id,
                        qty,
                        price,
                        fee,
                        fill_dt.isoformat(),
                    ),
                )

                if self._sqlite_changes() == 0:
                    # 왜(why): 동일 fill_id 재전송은 중복 반영을 유발하므로 즉시 반환한다.
                    self._conn.commit()
                    committed = True
                    return self._build_result(order_id)

                agg = self._conn.execute(
                    """
                    SELECT
                        COALESCE(SUM(qty), 0) AS filled_qty,
                        COALESCE(SUM(qty * price), 0.0) AS weighted_notional,
                        COALESCE(SUM(fee), 0.0) AS total_fees
                    FROM fills
                    WHERE order_id = ?
                    """,
                    (order_id,),
                ).fetchone()

                filled_qty = int(agg["filled_qty"])
                weighted_notional = float(agg["weighted_notional"])
                total_fees = float(agg["total_fees"])
                avg_fill_price = (
                    weighted_notional / filled_qty if filled_qty > 0 else 0.0
                )

                order_qty = int(order_row["qty"])
                new_status = (
                    OrderStatus.FILLED
                    if filled_qty >= order_qty
                    else OrderStatus.PARTIAL
                )

                self._conn.execute(
                    """
                    UPDATE orders
                    SET
                        status = ?,
                        filled_qty = ?,
                        avg_fill_price = ?,
                        total_fees = ?,
                        updated_at = ?
                    WHERE order_id = ?
                    """,
                    (
                        new_status.value,
                        filled_qty,
                        avg_fill_price,
                        total_fees,
                        self._utc_now_iso(),
                        order_id,
                    ),
                )
                self._conn.commit()
                committed = True
            except Exception:
                if not committed:
                    self._conn.rollback()
                raise

            result = self._build_result(order_id)
            if result is None:
                return None

            symbol = str(order_row["symbol"])
            side_value = str(order_row["side"])

        if self._portfolio is not None:
            from schemas.models import Side

            self._portfolio.apply_fill(
                symbol=symbol,
                side=Side(side_value),
                qty=qty,
                price=price,
                fee=fee,
            )

        self._log_event(
            order_id=order_id,
            event_type="fill",
            status=result.status,
            detail={
                "fill_id": fill_id,
                "qty": qty,
                "price": price,
                "fee": fee,
                "filled_at": fill_dt.isoformat(),
            },
        )
        return result

    def on_reject(self, order_id: str, reason: str = "") -> OrderResult | None:
        """주문 거부 처리.

        Args:
            order_id: 내부 주문 ID.
            reason: 거부 사유.

        Returns:
            전이 성공 시 업데이트된 OrderResult, 실패 시 None.
        """
        self._validate_non_empty(order_id, "order_id")
        result = self._transition(
            order_id=order_id,
            new_status=OrderStatus.REJECTED,
            message=reason,
        )
        if result is not None:
            self._log_event(
                order_id=order_id,
                event_type="reject",
                status=result.status,
                detail={"reason": reason},
            )
        return result

    def on_cancel(self, order_id: str, reason: str = "") -> OrderResult | None:
        """주문 취소 처리.

        Args:
            order_id: 내부 주문 ID.
            reason: 취소 사유.

        Returns:
            전이 성공 시 업데이트된 OrderResult, 실패 시 None.
        """
        self._validate_non_empty(order_id, "order_id")
        result = self._transition(
            order_id=order_id,
            new_status=OrderStatus.CANCELED,
            message=reason,
        )
        if result is not None:
            self._log_event(
                order_id=order_id,
                event_type="cancel",
                status=result.status,
                detail={"reason": reason},
            )
        return result

    def get_order(self, order_id: str) -> OrderResult | None:
        """주문 조회.

        Args:
            order_id: 내부 주문 ID.

        Returns:
            OrderResult 또는 None.

        Raises:
            ValueError: order_id가 비어있으면 발생.
        """
        self._validate_non_empty(order_id, "order_id")
        with self._lock:
            return self._build_result(order_id)

    def get_open_orders(self) -> list[OrderResult]:
        """미종결 주문 목록 조회.

        Returns:
            상태가 NEW/SUBMITTED/PARTIAL인 주문 목록.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT order_id
                FROM orders
                WHERE status IN (?, ?, ?)
                ORDER BY created_at ASC
                """,
                self._OPEN_STATUSES,
            ).fetchall()

            results: list[OrderResult] = []
            for row in rows:
                result = self._build_result(str(row["order_id"]))
                if result is not None:
                    results.append(result)
            return results

    def recover_open_orders(self) -> list[OrderResult]:
        """재시작 복구용 미종결 주문 목록 조회.

        Returns:
            get_open_orders와 동일한 결과.
        """
        return self.get_open_orders()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ExecutionOMS":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _configure_sqlite(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.commit()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL,
                order_type TEXT NOT NULL DEFAULT 'LIMIT',
                status TEXT NOT NULL DEFAULT 'NEW',
                filled_qty INTEGER NOT NULL DEFAULT 0,
                avg_fill_price REAL NOT NULL DEFAULT 0.0,
                total_fees REAL NOT NULL DEFAULT 0.0,
                broker_order_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fills (
                order_id TEXT NOT NULL,
                fill_id TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0.0,
                filled_at TEXT NOT NULL,
                PRIMARY KEY (order_id, fill_id),
                FOREIGN KEY (order_id) REFERENCES orders(order_id)
            )
            """
        )
        self._conn.commit()

    def _build_result(self, order_id: str) -> OrderResult | None:
        """DB 레코드를 OrderResult로 hydrate.

        Args:
            order_id: 내부 주문 ID.

        Returns:
            OrderResult 또는 None.
        """
        order_row = self._get_order_row(order_id)
        if order_row is None:
            return None

        fill_rows = self._conn.execute(
            """
            SELECT fill_id, qty, price, fee, filled_at
            FROM fills
            WHERE order_id = ?
            ORDER BY filled_at ASC, fill_id ASC
            """,
            (order_id,),
        ).fetchall()

        fills = [
            Fill(
                fill_id=str(row["fill_id"]),
                qty=int(row["qty"]),
                price=float(row["price"]),
                fee=float(row["fee"]),
                timestamp=self._parse_utc_iso(str(row["filled_at"])),
            )
            for row in fill_rows
        ]

        broker_order_id_raw = order_row["broker_order_id"]
        message_raw = order_row["message"]

        return OrderResult(
            trace_id=UUID(str(order_row["trace_id"])),
            broker_order_id=str(broker_order_id_raw or ""),
            idempotency_key=UUID(str(order_row["idempotency_key"])),
            status=OrderStatus(str(order_row["status"])),
            fills=fills,
            fees=float(order_row["total_fees"]),
            message=(str(message_raw) if message_raw else None),
        )

    def _transition(
        self,
        order_id: str,
        new_status: OrderStatus,
        message: str = "",
        **kwargs: Any,
    ) -> OrderResult | None:
        """상태 전이 공통 처리.

        Args:
            order_id: 내부 주문 ID.
            new_status: 목표 상태.
            message: 상태 메시지.
            **kwargs: 추가 업데이트 컬럼 (예: broker_order_id).

        Returns:
            전이 성공 시 OrderResult, 실패 시 None.
        """
        with self._lock:
            order_row = self._get_order_row(order_id)
            if order_row is None:
                return None

            current_status = OrderStatus(str(order_row["status"]))
            if current_status in TERMINAL_STATES:
                return None

            allowed = VALID_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                return None

            updates: dict[str, Any] = {
                "status": new_status.value,
                "updated_at": self._utc_now_iso(),
                "message": message,
            }
            updates.update(kwargs)

            set_clause = ", ".join(f"{column} = ?" for column in updates)
            values = list(updates.values()) + [order_id]

            self._conn.execute(
                f"UPDATE orders SET {set_clause} WHERE order_id = ?",
                values,
            )
            self._conn.commit()
            return self._build_result(order_id)

    def _get_order_row(self, order_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()

    def _sqlite_changes(self) -> int:
        row = self._conn.execute("SELECT changes() AS count").fetchone()
        if row is None:
            return 0
        return int(row["count"])

    def _log_event(
        self,
        order_id: str,
        event_type: str,
        status: OrderStatus,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._logger is None:
            return
        event = OrderEvent(
            order_id=order_id,
            event_type=event_type,
            status=status,
            detail=detail or {},
        )
        self._logger.log(event, event_type="OrderEvent")

    @staticmethod
    def _validate_non_empty(value: str, name: str) -> None:
        """빈 문자열 입력 검증.

        Args:
            value: 검증 대상 문자열.
            name: 필드명.

        Raises:
            ValueError: 공백/빈 문자열이면 발생.
        """
        if not value or not value.strip():
            raise ValueError(f"{name}는 비어 있을 수 없습니다")

    @staticmethod
    def _ensure_utc_aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_utc_iso(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
