from tools.yfinance_client import history, info
import pandas as pd


def test_history_returns_df():
    df = history("AAPL", period="1mo")
    assert isinstance(df, pd.DataFrame)


def test_info_returns_dict():
    d = info("AAPL")
    assert isinstance(d, dict) and d.get("ticker") == "AAPL"
