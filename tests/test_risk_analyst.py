import pandas as pd
from agents.risk_analyst import correlate


def test_correlate_produces_matrix():
    res = correlate(["AAPL", "MSFT"])  # 무료 경로
    corr = res.get("corr")
    assert isinstance(corr, pd.DataFrame)
    if not corr.empty:
        assert list(corr.columns) == list(corr.index)
