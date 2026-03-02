"""
tests/test_kill_switch.py - KillSwitch 단위 테스트.

12개 테스트: 기본 상태, 활성화/비활성화, 이력, 스레드 안전성, 엣지 케이스.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from engine.kill_switch import KillSwitch, KillSwitchLevel


def test_default_disarmed():
    """새 인스턴스는 DISARMED 상태."""
    ks = KillSwitch()
    assert ks.level == KillSwitchLevel.DISARMED
    assert not ks.is_active
    assert ks.reason == ""
    assert ks.activated_at is None
    assert ks.history == []


def test_activate_pause():
    """activate(PAUSE) → is_active True, level PAUSE."""
    ks = KillSwitch()
    ks.activate(KillSwitchLevel.PAUSE, reason="MDD breach")
    assert ks.is_active
    assert ks.level == KillSwitchLevel.PAUSE
    assert ks.reason == "MDD breach"


def test_deactivate():
    """activate → deactivate → DISARMED."""
    ks = KillSwitch()
    ks.activate(KillSwitchLevel.PAUSE, reason="test")
    ks.deactivate(reason="resolved")
    assert not ks.is_active
    assert ks.level == KillSwitchLevel.DISARMED
    assert ks.reason == ""
    assert ks.activated_at is None


def test_is_active_false_when_disarmed():
    """DISARMED 상태에서 is_active는 False."""
    ks = KillSwitch()
    assert not ks.is_active


def test_activate_with_reason():
    """활성화 시 reason이 보존된다."""
    ks = KillSwitch()
    ks.activate(KillSwitchLevel.PAUSE, reason="Monitor STOP: MDD 7.5% >= 한도 7.0%")
    assert "MDD" in ks.reason


def test_activated_at_timestamp():
    """활성화 시 타임스탬프가 설정된다."""
    ks = KillSwitch()
    before = datetime.now(timezone.utc)
    ks.activate(KillSwitchLevel.PAUSE, reason="test")
    after = datetime.now(timezone.utc)
    assert ks.activated_at is not None
    assert before <= ks.activated_at <= after


def test_deactivate_when_already_disarmed():
    """이미 DISARMED이면 deactivate는 noop (에러 없음)."""
    ks = KillSwitch()
    ks.deactivate(reason="noop")  # should not raise
    assert not ks.is_active
    assert ks.history == []  # noop이므로 이력에 기록 안 됨


def test_activate_disarmed_raises():
    """activate(DISARMED)는 ValueError 발생."""
    ks = KillSwitch()
    with pytest.raises(ValueError, match="Cannot activate with DISARMED"):
        ks.activate(KillSwitchLevel.DISARMED)


def test_history_tracking():
    """activate/deactivate가 이력에 기록된다."""
    ks = KillSwitch()
    ks.activate(KillSwitchLevel.PAUSE, reason="r1")
    ks.deactivate(reason="r2")
    ks.activate(KillSwitchLevel.PAUSE, reason="r3")

    history = ks.history
    assert len(history) == 3
    assert history[0]["action"] == "activate"
    assert history[0]["level"] == "PAUSE"
    assert history[0]["reason"] == "r1"
    assert history[1]["action"] == "deactivate"
    assert history[1]["reason"] == "r2"
    assert history[2]["action"] == "activate"
    assert history[2]["reason"] == "r3"


def test_thread_safety():
    """멀티스레드에서 activate/deactivate가 안전하게 동작한다."""
    ks = KillSwitch()
    errors: list[Exception] = []

    def activate_loop():
        try:
            for _ in range(100):
                ks.activate(KillSwitchLevel.PAUSE, reason="thread")
                ks.deactivate(reason="thread")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=activate_loop) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # 최종 상태는 DISARMED (모든 스레드가 deactivate로 끝남)
    assert not ks.is_active


def test_crash_safe_default():
    """새 인스턴스는 이전 상태와 무관하게 항상 DISARMED."""
    ks1 = KillSwitch()
    ks1.activate(KillSwitchLevel.PAUSE, reason="crash")

    # 시뮬레이션: 프로세스 재시작 → 새 인스턴스 생성
    ks2 = KillSwitch()
    assert not ks2.is_active
    assert ks2.level == KillSwitchLevel.DISARMED


def test_reactivate_updates_reason():
    """이미 활성화 상태에서 재활성화하면 reason이 업데이트된다."""
    ks = KillSwitch()
    ks.activate(KillSwitchLevel.PAUSE, reason="first")
    assert ks.reason == "first"
    ks.activate(KillSwitchLevel.PAUSE, reason="second")
    assert ks.reason == "second"
    assert len(ks.history) == 2
