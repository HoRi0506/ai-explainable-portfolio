import pandas as pd
from agents.risk_analyst import correlate


def test_correlate_empty_input():
    res = correlate([])
    corr = res.get("corr")
    assert isinstance(corr, pd.DataFrame)

