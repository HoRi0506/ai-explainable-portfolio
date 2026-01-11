from agents.allocator import allocate


def test_allocate_respects_bounds_and_sum():
    tickers = ["A", "B", "C"]
    constraints = {"min_weight": 0.0, "max_weight": 0.6}
    w = allocate(tickers, constraints)
    assert set(w.keys()) == set(tickers)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(0.0 <= v <= 0.6 for v in w.values())


def test_allocate_tight_max():
    tickers = ["A", "B", "C"]
    constraints = {"min_weight": 0.0, "max_weight": 0.34}
    w = allocate(tickers, constraints)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert all(v <= 0.34 + 1e-9 for v in w.values())
