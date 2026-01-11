from app.graph import run_demo


def test_run_demo_minimal():
    res = run_demo({"sectors": ["AI"], "budget": 1000000})
    assert isinstance(res, dict)
    assert "tickers" in res and isinstance(res["tickers"], list)
    assert "weights" in res and isinstance(res["weights"], dict)

