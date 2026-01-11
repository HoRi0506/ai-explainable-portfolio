from agents.trade_planner import plan


def test_plan_basic_shapes():
    weights = {"AAPL": 0.5, "MSFT": 0.5}
    orders = plan(weights, budget=1_000_000, fee_bps=3, slippage_bps=10)
    assert isinstance(orders, list) and len(orders) == 2
    for o in orders:
        assert set(["ticker", "qty", "est_price", "fee", "total"]).issubset(o.keys())
        assert isinstance(o["qty"], int) and o["qty"] >= 0
