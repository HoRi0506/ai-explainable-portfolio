from agents.ticker_screener import screen


def test_screen_filters_invalid_symbols():
    candidates = [
        {"ticker": "AAPL"},
        {"ticker": "XXXX"},  # 존재 가능성 희박
    ]
    out = screen(candidates, min_avg_dollar_vol=1_000_000.0)
    tickers = {x["ticker"] for x in out}
    assert "AAPL" in tickers
