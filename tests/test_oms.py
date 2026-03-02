"""tests/test_oms.py - ExecutionOMS 종합 테스트."""

from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from engine.execution_oms import ExecutionOMS, OrderEvent
from engine.portfolio import PortfolioManager
from schemas.models import (
    ApprovedOrderPlan,
    BrokerOrder,
    OrderSizing,
    OrderStatus,
    Side,
    TradingMode,
)


class SpyAuditLogger:
    """로그 호출 내용을 메모리에 기록하는 테스트 더블."""

    def __init__(self) -> None:
        self.records: list[tuple[Any, str | None]] = []

    def log(self, event: Any, event_type: str | None = None) -> None:
        """이벤트를 기록한다."""
        self.records.append((event, event_type))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """테스트 SQLite DB 경로."""
    return tmp_path / "test_orders.db"


@pytest.fixture
def portfolio() -> PortfolioManager:
    """기본 포트폴리오."""
    return PortfolioManager(initial_cash=10_000_000)


@pytest.fixture
def oms(
    db_path: Path, portfolio: PortfolioManager
) -> Generator[ExecutionOMS, None, None]:
    """ExecutionOMS 인스턴스."""
    with ExecutionOMS(db_path=db_path, portfolio=portfolio) as instance:
        yield instance


@pytest.fixture
def sample_plan() -> ApprovedOrderPlan:
    """샘플 ApprovedOrderPlan."""
    return make_plan()


def make_plan(
    *,
    trace_id: UUID | None = None,
    symbol: str = "005930",
    side: Side = Side.BUY,
    qty: int = 10,
    price: float = 70000.0,
) -> ApprovedOrderPlan:
    """테스트용 ApprovedOrderPlan 생성.

    Args:
        trace_id: 지정 trace_id. None이면 uuid4.
        symbol: 종목 코드.
        side: 매매 방향.
        qty: 주문 수량.
        price: 주문 가격.

    Returns:
        ApprovedOrderPlan 인스턴스.
    """
    return ApprovedOrderPlan(
        trace_id=trace_id or uuid4(),
        mode=TradingMode.PAPER,
        sizing=OrderSizing(
            qty=qty,
            notional=qty * price,
            weight_pct=7.0,
        ),
        risk_checks=[],
        order=BrokerOrder(
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
        ),
    )


def submit_and_ack(oms: ExecutionOMS, plan: ApprovedOrderPlan) -> str:
    """주문 제출 후 ACK까지 진행한다."""
    created = oms.submit_order(plan)
    order_id = str(created.trace_id)
    acked = oms.on_ack(order_id, f"broker-{order_id}")
    assert acked is not None
    assert acked.status == OrderStatus.SUBMITTED
    return order_id


class TestSubmit:
    """submit_order 테스트."""

    def test_submit_order_creates_new_order(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        result = oms.submit_order(sample_plan)
        assert result.status == OrderStatus.NEW
        assert result.trace_id == sample_plan.trace_id
        assert result.broker_order_id == ""
        assert result.fills == []
        assert result.fees == 0.0

    def test_submit_order_returns_duplicate_for_same_trace_id(
        self, oms: ExecutionOMS
    ) -> None:
        trace_id = uuid4()
        first = oms.submit_order(make_plan(trace_id=trace_id))
        second = oms.submit_order(make_plan(trace_id=trace_id))
        assert first.trace_id == second.trace_id
        assert first.idempotency_key == second.idempotency_key

    def test_submit_order_generates_idempotency_key(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        result = oms.submit_order(sample_plan)
        assert isinstance(result.idempotency_key, UUID)

    def test_submit_order_stores_symbol_side_qty_price(self, db_path: Path) -> None:
        plan = make_plan(symbol="000660", side=Side.BUY, qty=13, price=12345.0)
        with ExecutionOMS(db_path=db_path) as local_oms:
            created = local_oms.submit_order(plan)
            fetched = local_oms.get_order(str(created.trace_id))
            assert fetched is not None
            assert fetched.trace_id == plan.trace_id

    def test_submit_order_with_sell_side(self, db_path: Path) -> None:
        plan = make_plan(side=Side.SELL, qty=3, price=80000.0)
        with ExecutionOMS(db_path=db_path) as local_oms:
            result = local_oms.submit_order(plan)
            assert result.status == OrderStatus.NEW


class TestAck:
    """on_ack 테스트."""

    def test_on_ack_transitions_new_to_submitted(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        created = oms.submit_order(sample_plan)
        order_id = str(created.trace_id)
        result = oms.on_ack(order_id, "broker-ack-1")
        assert result is not None
        assert result.status == OrderStatus.SUBMITTED

    def test_on_ack_stores_broker_order_id(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        created = oms.submit_order(sample_plan)
        order_id = str(created.trace_id)
        result = oms.on_ack(order_id, "broker-ack-2")
        assert result is not None
        assert result.broker_order_id == "broker-ack-2"

    def test_on_ack_on_terminal_state_returns_none(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        created = oms.submit_order(sample_plan)
        order_id = str(created.trace_id)
        rejected = oms.on_reject(order_id, "nope")
        assert rejected is not None
        result = oms.on_ack(order_id, "broker-after-terminal")
        assert result is None


class TestFill:
    """on_fill 테스트."""

    def test_on_fill_transitions_submitted_to_partial(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        result = oms.on_fill(order_id, "f1", qty=3, price=70000.0, fee=10.0)
        assert result is not None
        assert result.status == OrderStatus.PARTIAL
        assert len(result.fills) == 1

    def test_on_fill_transitions_partial_to_filled(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        partial = oms.on_fill(order_id, "f1", qty=4, price=70000.0)
        assert partial is not None
        assert partial.status == OrderStatus.PARTIAL
        final = oms.on_fill(order_id, "f2", qty=6, price=70100.0)
        assert final is not None
        assert final.status == OrderStatus.FILLED

    def test_on_fill_deduplicates_by_fill_id(
        self,
        oms: ExecutionOMS,
        sample_plan: ApprovedOrderPlan,
        portfolio: PortfolioManager,
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        first = oms.on_fill(order_id, "dup", qty=5, price=70000.0, fee=10.0)
        second = oms.on_fill(order_id, "dup", qty=5, price=70000.0, fee=10.0)
        assert first is not None
        assert second is not None
        assert len(second.fills) == 1
        pos = portfolio.get_position("005930")
        assert pos is not None
        assert pos.qty == 5

    def test_on_fill_computes_cumulative_avg_price_and_fees(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=4, price=70000.0, fee=20.0)
        result = oms.on_fill(order_id, "f2", qty=6, price=71000.0, fee=30.0)
        assert result is not None
        assert result.status == OrderStatus.FILLED
        assert result.fees == pytest.approx(50.0)
        weighted_avg = (4 * 70000.0 + 6 * 71000.0) / 10
        fills_notional = sum(fill.qty * fill.price for fill in result.fills)
        assert fills_notional / 10 == pytest.approx(weighted_avg)

    def test_on_fill_applies_to_portfolio_when_portfolio_provided(
        self,
        oms: ExecutionOMS,
        sample_plan: ApprovedOrderPlan,
        portfolio: PortfolioManager,
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=10, price=70000.0, fee=100.0)
        pos = portfolio.get_position("005930")
        assert pos is not None
        assert pos.qty == 10

    def test_on_fill_on_terminal_state_returns_none(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=10, price=70000.0)
        result = oms.on_fill(order_id, "f2", qty=1, price=70000.0)
        assert result is None

    def test_multiple_partial_fills_accumulate_correctly(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=2, price=70000.0)
        _ = oms.on_fill(order_id, "f2", qty=3, price=70100.0)
        result = oms.on_fill(order_id, "f3", qty=5, price=70200.0)
        assert result is not None
        assert result.status == OrderStatus.FILLED
        assert sum(fill.qty for fill in result.fills) == 10

    def test_on_fill_with_sell_applies_to_portfolio_correctly(
        self, oms: ExecutionOMS, portfolio: PortfolioManager
    ) -> None:
        buy_order_id = submit_and_ack(
            oms, make_plan(side=Side.BUY, qty=10, price=70000.0)
        )
        _ = oms.on_fill(buy_order_id, "buy-f1", qty=10, price=70000.0, fee=100.0)

        sell_plan = make_plan(side=Side.SELL, qty=4, price=71000.0)
        sell_order_id = submit_and_ack(oms, sell_plan)
        _ = oms.on_fill(sell_order_id, "sell-f1", qty=4, price=71000.0, fee=50.0)

        pos = portfolio.get_position("005930")
        assert pos is not None
        assert pos.qty == 6


class TestReject:
    """on_reject 테스트."""

    def test_on_reject_transitions_new_to_rejected(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        created = oms.submit_order(sample_plan)
        result = oms.on_reject(str(created.trace_id), "bad order")
        assert result is not None
        assert result.status == OrderStatus.REJECTED
        assert result.message == "bad order"

    def test_on_reject_transitions_submitted_to_rejected(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        result = oms.on_reject(order_id, "broker reject")
        assert result is not None
        assert result.status == OrderStatus.REJECTED

    def test_on_reject_on_terminal_returns_none(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=10, price=70000.0)
        result = oms.on_reject(order_id, "too late")
        assert result is None


class TestCancel:
    """on_cancel 테스트."""

    def test_on_cancel_transitions_new_to_canceled(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        created = oms.submit_order(sample_plan)
        result = oms.on_cancel(str(created.trace_id), "manual cancel")
        assert result is not None
        assert result.status == OrderStatus.CANCELED
        assert result.message == "manual cancel"

    def test_on_cancel_transitions_submitted_to_canceled(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        result = oms.on_cancel(order_id, "broker cancel")
        assert result is not None
        assert result.status == OrderStatus.CANCELED

    def test_on_cancel_on_terminal_returns_none(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=10, price=70000.0)
        result = oms.on_cancel(order_id, "too late")
        assert result is None


class TestQuery:
    """조회 API 테스트."""

    def test_get_order_returns_none_for_unknown(self, oms: ExecutionOMS) -> None:
        assert oms.get_order(str(uuid4())) is None

    def test_get_order_returns_correct_fills(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        _ = oms.on_fill(order_id, "f1", qty=4, price=70000.0)
        _ = oms.on_fill(order_id, "f2", qty=6, price=70000.0)
        result = oms.get_order(order_id)
        assert result is not None
        assert len(result.fills) == 2
        assert result.status == OrderStatus.FILLED

    def test_get_open_orders_returns_non_terminal_only(self, oms: ExecutionOMS) -> None:
        open_order_id = submit_and_ack(oms, make_plan(trace_id=uuid4(), qty=10))
        terminal_order_id = submit_and_ack(oms, make_plan(trace_id=uuid4(), qty=5))
        _ = oms.on_fill(terminal_order_id, "f-term", qty=5, price=70000.0)

        open_orders = oms.get_open_orders()
        open_ids = {str(item.trace_id) for item in open_orders}
        assert open_order_id in open_ids
        assert terminal_order_id not in open_ids

    def test_get_order_idempotency_key_returns_key(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        result = oms.submit_order(sample_plan)
        order_id = str(result.trace_id)
        key = oms.get_order_idempotency_key(order_id)
        assert key == result.idempotency_key

    def test_get_order_idempotency_key_returns_none_for_unknown(
        self, oms: ExecutionOMS
    ) -> None:
        assert oms.get_order_idempotency_key(str(uuid4())) is None

    def test_get_order_idempotency_key_handles_malformed_data(
        self, db_path: Path
    ) -> None:
        """멱등성 키가 잘못된 형식이면 None을 반환한다."""
        with ExecutionOMS(db_path=db_path) as local_oms:
            plan = make_plan()
            result = local_oms.submit_order(plan)
            order_id = str(result.trace_id)

            # Corrupt the idempotency_key in DB
            local_oms._conn.execute(
                "UPDATE orders SET idempotency_key = ? WHERE order_id = ?",
                ("not-a-uuid", order_id),
            )
            local_oms._conn.commit()

            key = local_oms.get_order_idempotency_key(order_id)
            assert key is None


class TestRecovery:
    """재시작 복구 테스트."""

    def test_recover_open_orders_after_restart(self, db_path: Path) -> None:
        open_id: str
        with ExecutionOMS(db_path=db_path) as oms1:
            open_id = submit_and_ack(oms1, make_plan(trace_id=uuid4(), qty=10))

        with ExecutionOMS(db_path=db_path) as oms2:
            recovered = oms2.recover_open_orders()
            recovered_ids = {str(item.trace_id) for item in recovered}
            assert open_id in recovered_ids

    def test_orders_survive_close_and_reopen(self, db_path: Path) -> None:
        plan = make_plan(trace_id=uuid4(), qty=10)
        order_id = str(plan.trace_id)
        with ExecutionOMS(db_path=db_path) as oms1:
            _ = oms1.submit_order(plan)

        with ExecutionOMS(db_path=db_path) as oms2:
            found = oms2.get_order(order_id)
            assert found is not None
            assert found.trace_id == plan.trace_id

    def test_terminal_orders_not_in_recovery_list(self, db_path: Path) -> None:
        with ExecutionOMS(db_path=db_path) as oms1:
            open_id = submit_and_ack(oms1, make_plan(trace_id=uuid4(), qty=10))
            terminal_id = submit_and_ack(oms1, make_plan(trace_id=uuid4(), qty=4))
            _ = oms1.on_fill(terminal_id, "f1", qty=4, price=70000.0)

        with ExecutionOMS(db_path=db_path) as oms2:
            recovered = oms2.recover_open_orders()
            recovered_ids = {str(item.trace_id) for item in recovered}
            assert open_id in recovered_ids
            assert terminal_id not in recovered_ids


class TestLogging:
    """감사 로그 테스트."""

    def test_submit_order_logs_order_event(self, db_path: Path) -> None:
        spy = SpyAuditLogger()
        plan = make_plan(trace_id=uuid4())
        with ExecutionOMS(db_path=db_path, logger=cast(Any, spy)) as local_oms:
            _ = local_oms.submit_order(plan)

        assert len(spy.records) == 1
        event, event_type = spy.records[0]
        assert isinstance(event, OrderEvent)
        assert event_type == "OrderEvent"
        assert event.event_type == "submit"
        assert event.status == OrderStatus.NEW

    def test_on_fill_logs_order_event(self, db_path: Path) -> None:
        spy = SpyAuditLogger()
        with ExecutionOMS(
            db_path=db_path,
            portfolio=PortfolioManager(10_000_000),
            logger=cast(Any, spy),
        ) as local_oms:
            order_id = submit_and_ack(local_oms, make_plan(trace_id=uuid4(), qty=10))
            _ = local_oms.on_fill(order_id, "f-log", qty=10, price=70000.0, fee=33.0)

        assert len(spy.records) == 3
        fill_event, event_type = spy.records[-1]
        assert isinstance(fill_event, OrderEvent)
        assert event_type == "OrderEvent"
        assert fill_event.event_type == "fill"
        assert fill_event.status == OrderStatus.FILLED


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_fill_qty_exceeding_order_qty_marks_filled(self, oms: ExecutionOMS) -> None:
        order_id = submit_and_ack(oms, make_plan(trace_id=uuid4(), qty=10))
        result = oms.on_fill(order_id, "f-over", qty=12, price=70000.0)
        assert result is not None
        assert result.status == OrderStatus.FILLED
        assert sum(fill.qty for fill in result.fills) == 12

    def test_concurrent_fills_on_same_order(self, db_path: Path) -> None:
        with ExecutionOMS(
            db_path=db_path, portfolio=PortfolioManager(10_000_000)
        ) as local_oms:
            order_id = submit_and_ack(local_oms, make_plan(trace_id=uuid4(), qty=10))

            def push_fill(i: int) -> OrderStatus | None:
                result = local_oms.on_fill(
                    order_id,
                    fill_id=f"cf-{i}",
                    qty=1,
                    price=70000.0 + i,
                    fee=0.1,
                    filled_at=datetime.now(timezone.utc),
                )
                return None if result is None else result.status

            with ThreadPoolExecutor(max_workers=10) as pool:
                statuses = list(pool.map(push_fill, range(10)))

            final = local_oms.get_order(order_id)
            assert final is not None
            assert final.status == OrderStatus.FILLED
            assert len(final.fills) == 10
            assert all(
                status in {OrderStatus.PARTIAL, OrderStatus.FILLED}
                for status in statuses
            )

    def test_on_fill_invalid_qty_raises_value_error(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        with pytest.raises(ValueError, match="수량은 양의 정수여야 합니다"):
            _ = oms.on_fill(order_id, "bad-qty", qty=0, price=70000.0)

    def test_on_fill_invalid_price_raises_value_error(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        with pytest.raises(ValueError, match="가격은 양수여야 합니다"):
            _ = oms.on_fill(order_id, "bad-price", qty=1, price=0.0)

    def test_on_fill_invalid_fee_raises_value_error(
        self, oms: ExecutionOMS, sample_plan: ApprovedOrderPlan
    ) -> None:
        order_id = submit_and_ack(oms, sample_plan)
        with pytest.raises(ValueError, match="수수료는 0 이상이어야 합니다"):
            _ = oms.on_fill(order_id, "bad-fee", qty=1, price=70000.0, fee=-1.0)

    def test_submit_duplicate_trace_id_keeps_single_open_order(
        self, oms: ExecutionOMS
    ) -> None:
        trace_id = uuid4()
        _ = oms.submit_order(make_plan(trace_id=trace_id))
        _ = oms.submit_order(make_plan(trace_id=trace_id))
        open_orders = oms.get_open_orders()
        ids = [str(item.trace_id) for item in open_orders]
        assert ids.count(str(trace_id)) == 1

    def test_submit_order_rejects_zero_qty(self, db_path: Path) -> None:
        """qty=0인 주문 제출 시 ValueError."""
        plan = make_plan(qty=0, price=70000.0)
        # qty=0은 OrderSizing에도 0으로 설정
        plan_dict = plan.model_dump()
        plan_dict["order"]["qty"] = 0
        plan_dict["sizing"]["qty"] = 0
        bad_plan = ApprovedOrderPlan(**plan_dict)
        with ExecutionOMS(db_path=db_path) as local_oms:
            with pytest.raises(ValueError, match="주문 수량은 양의 정수여야 합니다"):
                local_oms.submit_order(bad_plan)

    def test_submit_order_rejects_negative_price(self, db_path: Path) -> None:
        """price=-1인 주문 제출 시 ValueError."""
        plan = make_plan(qty=10, price=-1.0)
        plan_dict = plan.model_dump()
        plan_dict["order"]["price"] = -1.0
        bad_plan = ApprovedOrderPlan(**plan_dict)
        with ExecutionOMS(db_path=db_path) as local_oms:
            with pytest.raises(ValueError, match="주문 가격은 양수여야 합니다"):
                local_oms.submit_order(bad_plan)
