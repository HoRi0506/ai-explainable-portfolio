"""티커 스크리너
- yfinance 래퍼를 활용한 기본 유동성/거래 가능 필터
"""

from __future__ import annotations
from typing import List, Dict, Any
import math
from tools import yfinance_client as yfwrap


def screen(candidates: List[Dict[str, Any]], min_avg_dollar_vol: float = 5_000_000.0) -> List[Dict[str, Any]]:
    screened: List[Dict[str, Any]] = []
    for c in candidates:
        t = c.get("ticker")
        if not t:
            continue
        info = yfwrap.info(t)
        df = yfwrap.history(t, period="1mo")
        if df is None or df.empty:
            continue
        if "DollarVolume" in df.columns:
            avg_dv = float(df["DollarVolume"].tail(20).mean())
        else:
            # 보수적으로 제외
            continue
        if math.isnan(avg_dv) or avg_dv < min_avg_dollar_vol:
            continue
        out = dict(c)
        out.update({
            "exchange": info.get("exchange"),
            "avg_dollar_vol": avg_dv,
        })
        screened.append(out)
    return screened
