"""리스크 애널리스트
- yfinance 히스토리로 상관행렬 계산(일간 수익률)
"""

from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
from tools import yfinance_client as yfwrap


def correlate(tickers: List[str]) -> Dict[str, Any]:
    prices: Dict[str, pd.Series] = {}
    for t in tickers:
        h = yfwrap.history(t, period="6mo")
        if h is not None and not h.empty and "Close" in h.columns:
            prices[t] = h["Close"].astype(float)
    if not prices:
        df = pd.DataFrame()
        return {"corr": df, "risk_notes": "가격 데이터 부족"}
    aligned = pd.DataFrame(prices).dropna(how="any")
    if aligned.empty:
        return {"corr": pd.DataFrame(), "risk_notes": "정렬 후 데이터 부족"}
    returns = aligned.pct_change().dropna(how="any")
    corr = returns.corr()
    # 요약 노트: 최대 상관쌍 및 수치
    max_pair, max_val = _max_corr_pair(corr)
    note = f"최고 상관: {max_pair[0]}-{max_pair[1]} = {max_val:.2f}" if max_pair else "상관 계산 불가"
    return {"corr": corr, "risk_notes": note}


def _max_corr_pair(corr: pd.DataFrame):
    if corr is None or corr.empty:
        return None, float("nan")
    best = None
    best_val = -1.0
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = float(corr.iloc[i, j])
            if v > best_val:
                best_val = v
                best = (cols[i], cols[j])
    return best, best_val
