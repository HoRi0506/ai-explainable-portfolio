"""tests/test_circuit_breaker.py - CircuitBreaker 단위 테스트."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from engine.circuit_breaker import CircuitBreaker, CircuitState


class FakeClock:
    """테스트용 수동 시계."""

    def __init__(self, start: float = 0.0):
        self._time: float = start

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


def _trip_breaker(breaker: CircuitBreaker) -> None:
    """임계치까지 일시적 실패를 기록해 OPEN으로 만든다."""
    for _ in range(5):
        breaker.record_failure(transient=True)


def test_initial_state_is_closed() -> None:
    breaker = CircuitBreaker()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_transient_failures_below_threshold_stay_closed() -> None:
    breaker = CircuitBreaker(failure_threshold=5)
    for _ in range(4):
        breaker.record_failure(transient=True)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 4


def test_transient_failures_at_threshold_open_circuit() -> None:
    breaker = CircuitBreaker(failure_threshold=5)
    _trip_breaker(breaker)
    assert breaker.state == CircuitState.OPEN
    assert breaker.failure_count == 5


def test_deterministic_failure_does_not_trip_breaker() -> None:
    breaker = CircuitBreaker(failure_threshold=2)
    breaker.record_failure(transient=False)
    breaker.record_failure(transient=False)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_open_circuit_blocks_requests() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(cooldown_seconds=60.0, jitter_range=0.0, clock=clock)
    _trip_breaker(breaker)

    allowed, wait_seconds = breaker.before_request()
    assert allowed is False
    assert wait_seconds == 60.0


def test_open_to_half_open_after_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(cooldown_seconds=10.0, jitter_range=0.0, clock=clock)
    _trip_breaker(breaker)

    clock.advance(10.0)
    allowed, wait_seconds = breaker.before_request()
    assert allowed is True
    assert wait_seconds == 0.0
    assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_success_closes_circuit() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(cooldown_seconds=5.0, clock=clock)
    _trip_breaker(breaker)

    clock.advance(5.0)
    allowed, _ = breaker.before_request()
    assert allowed is True
    breaker.record_success()

    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_half_open_failure_reopens_circuit() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(cooldown_seconds=5.0, clock=clock)
    _trip_breaker(breaker)

    clock.advance(5.0)
    allowed, _ = breaker.before_request()
    assert allowed is True
    breaker.record_failure(transient=True)

    assert breaker.state == CircuitState.OPEN


def test_half_open_deterministic_failure_releases_probe() -> None:
    """HALF_OPEN 상태에서 결정론적 에러 시 probe가 해제되고 CLOSED로 복귀한다."""
    clock = FakeClock()
    breaker = CircuitBreaker(cooldown_seconds=5.0, clock=clock)
    _trip_breaker(breaker)

    clock.advance(5.0)
    allowed, _ = breaker.before_request()  # Enters HALF_OPEN, probe started
    assert allowed is True
    assert breaker.state == CircuitState.HALF_OPEN

    # Deterministic failure (transient=False) — broker responded, not an infra issue
    breaker.record_failure(transient=False)

    # Should close circuit (broker is reachable) and release probe
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0

    # Next request should be allowed immediately
    allowed2, wait2 = breaker.before_request()
    assert allowed2 is True
    assert wait2 == 0.0


def test_half_open_allows_only_one_probe() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(cooldown_seconds=3.0, jitter_range=0.0, clock=clock)
    _trip_breaker(breaker)
    clock.advance(3.0)

    def call_before_request() -> bool:
        return breaker.before_request()[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(call_before_request) for _ in range(8)]
        results = [future.result() for future in futures]

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_backoff_with_jitter_within_range() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=1.0,
        backoff_base=1.0,
        backoff_max=60.0,
        jitter_range=0.5,
        clock=clock,
    )
    for _ in range(3):
        breaker.record_failure(transient=True)

    clock.advance(1.0)
    allowed, wait = breaker.before_request()
    assert allowed is True
    assert wait == 0.0

    expected_base = 4.0
    lower = expected_base * 0.75
    upper = expected_base * 1.25

    for _ in range(100):
        allowed_probe, computed = breaker.before_request()
        assert allowed_probe is False
        assert lower <= computed <= upper


def test_record_success_resets_failure_count() -> None:
    breaker = CircuitBreaker()
    breaker.record_failure(transient=True)
    breaker.record_failure(transient=True)
    assert breaker.failure_count == 2

    breaker.record_success()
    assert breaker.failure_count == 0
    assert breaker.state == CircuitState.CLOSED


def test_injectable_clock() -> None:
    clock = FakeClock(start=100.0)
    breaker = CircuitBreaker(cooldown_seconds=20.0, jitter_range=0.0, clock=clock)
    _trip_breaker(breaker)

    allowed_now, wait_now = breaker.before_request()
    assert allowed_now is False
    assert wait_now == 20.0

    clock.advance(20.0)
    allowed_later, wait_later = breaker.before_request()
    assert allowed_later is True
    assert wait_later == 0.0
