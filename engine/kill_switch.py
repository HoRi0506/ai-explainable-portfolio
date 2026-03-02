"""
engine/kill_switch.py - 글로벌 킬 스위치.

3단계 킬 스위치 중 Level 1(PAUSE) 구현.
Crash-safe: 인메모리 전용, 프로세스 재시작 시 항상 DISARMED.
Thread-safe: threading.Lock 사용.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_MAX_HISTORY_SIZE = 1000


class KillSwitchLevel(str, Enum):
    """킬 스위치 레벨.

    DISARMED: 정상 운영 (거래 허용).
    PAUSE: Level 1 — 신호 중단 (신규 진입 금지, 기존 손절만 유지).
    """

    DISARMED = "DISARMED"
    PAUSE = "PAUSE"
    # 왜(why): Level 2(CANCEL), Level 3(FLATTEN)은 B-6b에서 추가 예정.
    # CANCEL = "CANCEL"
    # FLATTEN = "FLATTEN"


class KillSwitch:
    """글로벌 킬 스위치.

    모든 거래 메커니즘(TradingMode, MonitorAgent halt, Reconciliation freeze)을
    오버라이드하는 최상위 안전장치.

    Crash-safe: 인메모리 전용. 프로세스 재시작 시 항상 DISARMED.
    Thread-safe: 모든 상태 접근은 threading.Lock으로 보호.
    """

    def __init__(self) -> None:
        """킬 스위치 초기화. 항상 DISARMED 상태로 시작."""
        self._level: KillSwitchLevel = KillSwitchLevel.DISARMED
        self._reason: str = ""
        self._activated_at: datetime | None = None
        self._history: list[dict[str, Any]] = []
        self._lock: threading.Lock = threading.Lock()

    def activate(self, level: KillSwitchLevel, reason: str = "") -> None:
        """킬 스위치를 지정 레벨로 활성화한다.

        Args:
            level: 활성화할 레벨. DISARMED는 불가 (deactivate 사용).
            reason: 활성화 사유 (감사 로그용).

        Raises:
            ValueError: level이 DISARMED인 경우.
        """
        if level == KillSwitchLevel.DISARMED:
            raise ValueError("Cannot activate with DISARMED level. Use deactivate().")

        with self._lock:
            now = datetime.now(timezone.utc)
            prev_level = self._level
            self._level = level
            self._reason = reason
            self._activated_at = now
            self._append_history(
                {
                    "action": "activate",
                    "level": level.value,
                    "prev_level": prev_level.value,
                    "reason": reason,
                    "timestamp": now.isoformat(),
                }
            )
            logger.warning(
                "[KillSwitch] ACTIVATED level=%s reason=%s prev=%s",
                level.value,
                reason,
                prev_level.value,
            )

    def deactivate(self, reason: str = "") -> None:
        """킬 스위치를 비활성화(DISARMED)한다.

        이미 DISARMED 상태이면 아무 동작도 하지 않는다 (noop).

        Args:
            reason: 비활성화 사유 (감사 로그용).
        """
        with self._lock:
            if self._level == KillSwitchLevel.DISARMED:
                return  # 왜(why): 이미 비활성화 상태이면 이력에 불필요한 노이즈를 남기지 않는다.

            now = datetime.now(timezone.utc)
            prev_level = self._level
            self._level = KillSwitchLevel.DISARMED
            self._reason = ""
            self._activated_at = None
            self._append_history(
                {
                    "action": "deactivate",
                    "level": KillSwitchLevel.DISARMED.value,
                    "prev_level": prev_level.value,
                    "reason": reason,
                    "timestamp": now.isoformat(),
                }
            )
            logger.info(
                "[KillSwitch] DEACTIVATED prev=%s reason=%s",
                prev_level.value,
                reason,
            )

    @property
    def level(self) -> KillSwitchLevel:
        """현재 킬 스위치 레벨."""
        with self._lock:
            return self._level

    @property
    def is_active(self) -> bool:
        """킬 스위치 활성화 여부. DISARMED가 아니면 True."""
        with self._lock:
            return self._level != KillSwitchLevel.DISARMED

    @property
    def reason(self) -> str:
        """현재 활성화 사유. DISARMED이면 빈 문자열."""
        with self._lock:
            return self._reason

    @property
    def activated_at(self) -> datetime | None:
        """활성화 시각. DISARMED이면 None."""
        with self._lock:
            return self._activated_at

    @property
    def history(self) -> list[dict[str, Any]]:
        """활성화/비활성화 이력의 불변 복사본."""
        with self._lock:
            return list(self._history)

    def _append_history(self, entry: dict[str, Any]) -> None:
        """이력 항목을 추가한다. _MAX_HISTORY_SIZE 초과 시 오래된 항목 제거.

        왜(why): 무한 이력 증가를 방지하기 위해 FIFO 방식으로 오래된 항목을 제거한다.
        주의: 반드시 _lock 내에서 호출해야 한다.
        """
        self._history.append(entry)
        if len(self._history) > _MAX_HISTORY_SIZE:
            self._history = self._history[-_MAX_HISTORY_SIZE:]
