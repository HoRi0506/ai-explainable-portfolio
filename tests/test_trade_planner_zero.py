from agents.trade_planner import plan


def test_plan_with_zero_budget():
    orders = plan({"AAPL": 1.0}, budget=0.0)
    assert len(orders) == 1
    o = orders[0]
    assert o["qty"] == 0
    assert float(o["fee"]) >= 0.0
    assert float(o["total"]) >= 0.0

