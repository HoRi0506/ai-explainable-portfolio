"""yfinance 래퍼 (간단 버전)
- 안전한 info/history 로딩
"""

from __future__ import annotations
from typing import Dict, Any
import pandas as pd
import yfinance as yf


def info(ticker: str) -> Dict[str, Any]:
    try:
        t = yf.Ticker(ticker)
        fast = getattr(t, "fast_info", None)
        data: Dict[str, Any] = {"ticker": ticker}
        if fast:
            # 일부 필드만 노출 (None-safe)
            for key in ("last_price", "market_cap", "shares", "currency"):  # type: ignore[attr-defined]
                val = getattr(fast, key, None)
                data[key] = None if isinstance(val, (property,)) else val
        info_dict = t.info or {}
        for key in ("shortName", "longName", "sector", "exchange"):
            data[key] = info_dict.get(key)
        # 재무 관련 추가 필드 (가능한 경우만)
        for key in ("enterpriseValue", "freeCashflow", "ebitda", "totalRevenue", "totalDebt", "trailingPE", "trailingEps"):
            if key in info_dict:
                data[key] = info_dict.get(key)
        return data
    except Exception:
        return {"ticker": ticker}

def history(ticker: str, period: str = "2y") -> pd.DataFrame:
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=period)
        if df is None or df.empty:
            return pd.DataFrame()
        # 거래대금 컬럼 추가(가능 시)
        if "Close" in df.columns and "Volume" in df.columns:
            df["DollarVolume"] = df["Close"].astype(float) * df["Volume"].astype(float)
        return df
    except Exception:
        return pd.DataFrame()
