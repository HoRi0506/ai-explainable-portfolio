"""engine/circuit_breaker.py - 브로커 호출용 서킷 브레이커.

글로벌 backoff + circuit breaker로 레이트리밋 시 재시도 폭주를 방지한다.
인메모리 전용이며 프로세스 재시작 시 CLOSED로 리셋된다.
"""

from __future__ import annotations

from enum import Enum
import random
import threading
import time
from typing import Callable


class CircuitState(str, Enum):
    """서킷 브레이커 상태."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """서킷 브레이커가 OPEN 상태일 때 발생."""


class CircuitBreaker:
    """Thread-safe circuit breaker with exponential backoff and jitter.

    Args:
        failure_threshold: OPEN 전환까지 연속 일시적 실패 횟수.
        cooldown_seconds: OPEN -> HALF_OPEN 전환 대기 시간(초).
        backoff_base: 지수 backoff 기본 대기 시간(초).
        backoff_max: backoff 최대 대기 시간(초).
        jitter_range: jitter 범위 비율 0~1.
        clock: 시간 함수. 기본값은 time.monotonic.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        jitter_range: float = 0.5,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._failure_threshold: int = failure_threshold
        self._cooldown_seconds: float = cooldown_seconds
        self._backoff_base: float = backoff_base
        self._backoff_max: float = backoff_max
        self._jitter_range: float = jitter_range
        self._clock: Callable[[], float] = clock or time.monotonic

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float | None = None
        self._probe_in_flight: bool = False
        self._lock: threading.Lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """현재 서킷 상태를 반환한다."""
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """누적된 일시적 실패 카운트를 반환한다."""
        with self._lock:
            return self._failure_count

    def before_request(self) -> tuple[bool, float]:
        """브로커 호출 전 상태를 검사한다.

        Returns:
            (allowed, wait_seconds).
        """
        with self._lock:
            now = self._clock()
            if self._state == CircuitState.CLOSED:
                return True, 0.0

            if self._state == CircuitState.OPEN:
                if self._last_failure_time is None:
                    self._last_failure_time = now
                elapsed = now - self._last_failure_time
                if elapsed >= self._cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_flight = True
                    return True, 0.0

                remaining = max(self._cooldown_seconds - elapsed, 0.0)
                return False, self._apply_jitter(remaining)

            if self._probe_in_flight:
                return False, self._compute_backoff()

            self._probe_in_flight = True
            return True, 0.0

    def record_success(self) -> None:
        """브로커 호출 성공을 기록하고 CLOSED로 복귀한다."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            self._probe_in_flight = False

    def record_failure(self, *, transient: bool) -> None:
        """브로커 호출 실패를 기록한다.

        Args:
            transient: 일시적 실패 여부.
        """
        if not transient:
            # 왜(why): 결정론적 에러(4xx 비즈니스 에러)는 브로커가 응답한 것이므로
            # 서킷 카운터에는 영향 없지만, HALF_OPEN probe는 해제해야 한다.
            # 그렇지 않으면 probe_in_flight가 영구 true로 남아 서킷이 교착된다.
            with self._lock:
                if self._state == CircuitState.HALF_OPEN and self._probe_in_flight:
                    # 브로커가 응답했으므로 인프라는 정상 → CLOSED로 복귀
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._last_failure_time = None
                    self._probe_in_flight = False
            return

        with self._lock:
            self._failure_count += 1
            self._last_failure_time = self._clock()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._probe_in_flight = False
                return

            if (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self._failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._probe_in_flight = False

    def _compute_backoff(self) -> float:
        """현재 실패 카운트 기반 backoff 시간을 계산한다."""
        failures = max(self._failure_count, 1)
        multiplier = 1.0
        for _ in range(failures - 1):
            multiplier *= 2.0
        raw_backoff = self._backoff_base * multiplier
        if raw_backoff > self._backoff_max:
            raw_backoff = self._backoff_max
        return self._apply_jitter(raw_backoff)

    def _apply_jitter(self, value: float) -> float:
        """지정 값에 jitter를 적용한다."""
        lower = max(0.0, 1 - self._jitter_range / 2)
        upper = 1 + self._jitter_range / 2
        return value * random.uniform(lower, upper)
