"""
tests/test_monitor_agent.py - MonitorAgent 단위 테스트.

18개 테스트: 4개 규칙(급변/부실/MDD/일일손실) x 정상/위반 + 복합/halt/리셋/pruning/edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from agents.monitor_agent import MonitorAgent
from config.settings import AppConfig
from schemas.models import (
    AlertAction,
    AlertSeverity,
    MarketSnapshot,
    Portfolio,
    Position,
    RiskPolicy,
    Venue,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_config(
    threshold_pct: float = 5.0,
    window_minutes: int = 30,
    stale_minutes: int = 30,
) -> AppConfig:
    """테스트용 AppConfig 생성."""
    return AppConfig(
        monitor_price_change_threshold_pct=threshold_pct,
        monitor_price_change_window_minutes=window_minutes,
        monitor_stale_data_minutes=stale_minutes,
    )


def make_policy(
    max_drawdown_pct: float = 7.0,
    daily_loss_limit_pct: float = 1.5,
) -> RiskPolicy:
    """테스트용 RiskPolicy 생성."""
    return RiskPolicy(
        max_drawdown_pct=max_drawdown_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
    )


def make_portfolio(
    positions: list[Position] | None = None,
    cash: float = 10_000_000,
    total_value: float = 10_000_000,
    daily_pnl: float = 0,
    mdd: float = 0,
) -> Portfolio:
    """테스트용 Portfolio 생성."""
    return Portfolio(
        positions=positions or [],
        cash=cash,
        total_value=total_value,
        daily_pnl=daily_pnl,
        mdd=mdd,
        updated_at=NOW - timedelta(minutes=60),  # 왜(why): 포트폴리오가 오래되면 부실 데이터 점검을 낸다.
    )


def make_snapshot(
    symbol: str = "005930",
    price: float = 70000.0,
    ts: datetime | None = None,
) -> MarketSnapshot:
    """테스트용 MarketSnapshot 생성."""
    return MarketSnapshot(
        ts=ts or NOW,
        venue=Venue.KR,
        symbol=symbol,
        price=price,
    )


def make_agent(
    config: AppConfig | None = None,
    policy: RiskPolicy | None = None,
    portfolio: Portfolio | None = None,
) -> MonitorAgent:
    """테스트용 MonitorAgent 생성."""
    cfg = config or make_config()
    pol = policy or make_policy()
    pf = portfolio or make_portfolio()
    mock_logger = MagicMock()
    return MonitorAgent(
        config=cfg,
        policy=pol,
        portfolio_fn=lambda: pf,
        logger=mock_logger,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_positions_no_alerts():
    """빈 포트폴리오, 스냅샷 있음 → 알림 0개."""
    agent = make_agent()
    snapshots = [make_snapshot()]
    alerts = agent.check(snapshots, now=NOW)
    assert len(alerts) == 0


def test_mdd_within_limit():
    """MDD 5.0 < 한도 7.0 → MDD 알림 없음."""
    pf = make_portfolio(mdd=5.0)
    agent = make_agent(portfolio=pf)
    alerts = agent.check([], now=NOW)
    assert not any("MDD" in a.message for a in alerts)


def test_mdd_breach():
    """MDD 7.0 >= 한도 7.0 → CRITICAL/STOP."""
    pf = make_portfolio(mdd=7.0)
    agent = make_agent(portfolio=pf)
    alerts = agent.check([], now=NOW)
    mdd_alerts = [a for a in alerts if "MDD" in a.message]
    assert len(mdd_alerts) == 1
    assert mdd_alerts[0].severity == AlertSeverity.CRITICAL
    assert mdd_alerts[0].action == AlertAction.STOP


def test_daily_loss_within_limit():
    """일일 손실 1.0% < 한도 1.5% → 알림 없음."""
    pf = make_portfolio(daily_pnl=-100_000, total_value=10_000_000)
    agent = make_agent(portfolio=pf)
    alerts = agent.check([], now=NOW)
    assert not any("일일 손실" in a.message for a in alerts)


def test_daily_loss_breach():
    """일일 손실 2.0% >= 한도 1.5% → CRITICAL/STOP."""
    pf = make_portfolio(daily_pnl=-200_000, total_value=10_000_000)
    agent = make_agent(portfolio=pf)
    alerts = agent.check([], now=NOW)
    loss_alerts = [a for a in alerts if "일일 손실" in a.message]
    assert len(loss_alerts) == 1
    assert loss_alerts[0].severity == AlertSeverity.CRITICAL
    assert loss_alerts[0].action == AlertAction.STOP


def test_price_drop_detected():
    """가격 100 → 90 (10% 하락 >= 5%) → CRITICAL/STOP 급락."""
    agent = make_agent()
    t1 = NOW
    t2 = NOW + timedelta(minutes=10)
    snap1 = make_snapshot(symbol="005930", price=100.0, ts=t1)
    snap2 = make_snapshot(symbol="005930", price=90.0, ts=t2)

    agent.check([snap1], now=t1)  # baseline
    alerts = agent.check([snap2], now=t2)

    drop_alerts = [a for a in alerts if "급락" in a.message]
    assert len(drop_alerts) == 1
    assert drop_alerts[0].severity == AlertSeverity.CRITICAL
    assert drop_alerts[0].action == AlertAction.STOP


def test_price_surge_detected():
    """가격 100 → 110 (10% 상승 >= 5%) → HIGH/HOLD 급등."""
    agent = make_agent()
    t1 = NOW
    t2 = NOW + timedelta(minutes=10)
    snap1 = make_snapshot(symbol="005930", price=100.0, ts=t1)
    snap2 = make_snapshot(symbol="005930", price=110.0, ts=t2)

    agent.check([snap1], now=t1)  # baseline
    alerts = agent.check([snap2], now=t2)

    surge_alerts = [a for a in alerts if "급등" in a.message]
    assert len(surge_alerts) == 1
    assert surge_alerts[0].severity == AlertSeverity.HIGH
    assert surge_alerts[0].action == AlertAction.HOLD


def test_price_within_threshold():
    """가격 100 → 103 (3% < 5%) → 가격 알림 없음."""
    agent = make_agent()
    t1 = NOW
    t2 = NOW + timedelta(minutes=10)
    snap1 = make_snapshot(symbol="005930", price=100.0, ts=t1)
    snap2 = make_snapshot(symbol="005930", price=103.0, ts=t2)

    agent.check([snap1], now=t1)
    alerts = agent.check([snap2], now=t2)

    price_alerts = [a for a in alerts if "급락" in a.message or "급등" in a.message]
    assert len(price_alerts) == 0


def test_stale_data_for_held_position():
    """보유 종목 스냅샷이 40분 전 → HIGH/STOP 부실."""
    pos = Position(symbol="005930", qty=10, avg_price=70000.0)
    pf = make_portfolio(positions=[pos])
    agent = make_agent(portfolio=pf)

    old_ts = NOW - timedelta(minutes=40)
    snap = make_snapshot(symbol="005930", price=70000.0, ts=old_ts)
    alerts = agent.check([snap], now=NOW)

    stale_alerts = [a for a in alerts if "부실" in a.message]
    assert len(stale_alerts) == 1
    assert stale_alerts[0].severity == AlertSeverity.HIGH
    assert stale_alerts[0].action == AlertAction.STOP


def test_fresh_data_no_alert():
    """보유 종목 스냅샷이 5분 전 → 부실 알림 없음."""
    pos = Position(symbol="005930", qty=10, avg_price=70000.0)
    pf = make_portfolio(positions=[pos])
    agent = make_agent(portfolio=pf)

    fresh_ts = NOW - timedelta(minutes=5)
    snap = make_snapshot(symbol="005930", price=70000.0, ts=fresh_ts)
    alerts = agent.check([snap], now=NOW)

    stale_alerts = [a for a in alerts if "부실" in a.message or "누락" in a.message]
    assert len(stale_alerts) == 0


def test_missing_snapshot_for_position():
    """보유 종목에 대한 스냅샷이 아예 없음 → HIGH/STOP 누락."""
    pos = Position(symbol="005930", qty=10, avg_price=70000.0)
    pf = make_portfolio(positions=[pos])
    agent = make_agent(portfolio=pf)

    other_snap = make_snapshot(symbol="035720", price=50000.0)
    alerts = agent.check([other_snap], now=NOW)

    missing_alerts = [a for a in alerts if "누락" in a.message]
    assert len(missing_alerts) == 1
    assert missing_alerts[0].severity == AlertSeverity.HIGH
    assert missing_alerts[0].action == AlertAction.STOP


def test_halt_on_stop_action():
    """MDD 위반으로 STOP → is_halted() == True."""
    pf = make_portfolio(mdd=7.0)
    agent = make_agent(portfolio=pf)
    assert not agent.is_halted()

    agent.check([], now=NOW)
    assert agent.is_halted()


def test_halt_persists_across_checks():
    """halt 후 다시 check → 여전히 halted, 알림도 반환."""
    pf = make_portfolio(mdd=7.0)
    agent = make_agent(portfolio=pf)

    agent.check([], now=NOW)
    assert agent.is_halted()

    alerts = agent.check([], now=NOW)
    assert agent.is_halted()
    assert len(alerts) >= 1  # MDD 알림 계속 발생


def test_reset_halt():
    """halt → reset_halt() → is_halted() == False."""
    pf = make_portfolio(mdd=7.0)
    agent = make_agent(portfolio=pf)

    agent.check([], now=NOW)
    assert agent.is_halted()

    agent.reset_halt()
    assert not agent.is_halted()


def test_multiple_rules_simultaneous():
    """MDD 위반 + 일일 손실 위반 → 2개 알림 동시."""
    pf = make_portfolio(mdd=7.0, daily_pnl=-200_000, total_value=10_000_000)
    agent = make_agent(portfolio=pf)
    alerts = agent.check([], now=NOW)

    mdd_alerts = [a for a in alerts if "MDD" in a.message]
    loss_alerts = [a for a in alerts if "일일 손실" in a.message]
    assert len(mdd_alerts) >= 1
    assert len(loss_alerts) >= 1
    assert len(alerts) >= 2


def test_price_history_pruning():
    """40분 전 이력 삽입 + window=30분 → pruning되어 거짓 알림 없음."""
    agent = make_agent()
    old_ts = NOW - timedelta(minutes=40)
    # 왜(why): 직접 이력에 삽입하여 pruning 동작을 테스트한다.
    agent._price_history["005930"].append((old_ts, 100.0))

    snap = make_snapshot(symbol="005930", price=90.0, ts=NOW)
    alerts = agent.check([snap], now=NOW)

    # 40분 전 이력은 30분 창 밖이므로 pruning됨 → 첫 관측으로 취급 → 가격 알림 없음
    price_alerts = [a for a in alerts if "급락" in a.message or "급등" in a.message]
    assert len(price_alerts) == 0


def test_first_observation_no_false_alert():
    """최초 호출 시 어떤 가격이든 → 가격 변동 알림 없음."""
    agent = make_agent()
    snap = make_snapshot(symbol="005930", price=50000.0)
    alerts = agent.check([snap], now=NOW)

    price_alerts = [a for a in alerts if "급락" in a.message or "급등" in a.message]
    assert len(price_alerts) == 0


def test_zero_price_guard():
    """price=0으로 기록 후 다음 가격 → 나눗셈 오류 없음, 알림 없음."""
    agent = make_agent()
    t1 = NOW
    t2 = NOW + timedelta(minutes=10)
    snap_zero = make_snapshot(symbol="005930", price=0.0, ts=t1)
    snap_normal = make_snapshot(symbol="005930", price=70000.0, ts=t2)

    agent.check([snap_zero], now=t1)
    alerts = agent.check([snap_normal], now=t2)

    # old_price=0 → skip → no price alert
    price_alerts = [a for a in alerts if "급락" in a.message or "급등" in a.message]
    assert len(price_alerts) == 0
