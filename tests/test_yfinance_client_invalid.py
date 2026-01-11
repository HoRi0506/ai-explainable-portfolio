import pandas as pd
from tools.yfinance_client import history, info


def test_invalid_symbol_history_returns_df():
    df = history("XXXX", period="1mo")
    assert isinstance(df, pd.DataFrame)


def test_invalid_symbol_info_returns_dict():
    d = info("XXXX")
    assert isinstance(d, dict)

