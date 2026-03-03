from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from schemas.models import ApprovedOrderPlan


class BrokerAdapter(ABC):
    """추상 브로커 어댑터. 모든 브로커 구현체의 기반 클래스."""

    @abstractmethod
    def connect(self) -> None:
        """브로커 연결을 수립한다. API 인증 포함."""
        ...

    @abstractmethod
    def close(self) -> None:
        """브로커 연결을 종료한다."""
        ...

    @abstractmethod
    def get_balance(self) -> dict[str, Any]:
        """계좌 잔고를 조회한다.

        Returns:
            {"positions": [...], "cash": float, "total_value": float, "raw": ...}
        """
        ...

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        """보유 포지션 목록을 조회한다.

        Returns:
            [{"symbol": str, "qty": int, "avg_price": float, "unrealized_pnl": float, ...}, ...]
        """
        ...

    @abstractmethod
    def submit_order(
        self, plan: ApprovedOrderPlan, client_order_id: str | None = None
    ) -> dict[str, Any]:
        """주문을 제출한다.

        Returns:
            {"odno": str, ...} - 브로커 주문 ID 포함
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str, **kwargs: Any) -> dict[str, Any]:
        """주문을 취소한다.

        Args:
            order_id: 브로커 주문 ID.
            **kwargs: 브로커별 추가 파라미터 (예: KIS의 orgno).
        """
        ...

    @abstractmethod
    def get_order_status(self, order_id: str | None = None) -> list[dict[str, Any]]:
        """주문 상태를 조회한다. order_id가 None이면 전체 미체결 주문."""
        ...

    @abstractmethod
    def get_fills(self, date_str: str | None = None) -> list[dict[str, Any]]:
        """체결 내역을 조회한다. date_str이 None이면 오늘."""
        ...
