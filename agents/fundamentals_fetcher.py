"""펀더멘털 페처
- yfinance info/history로 재무치 수집(가능 범위)
- SEC filings 링크를 출처로 보강(가능 시)
- 실패 시 None-safe로 폴백
"""

from __future__ import annotations
from typing import Dict, Any, List
from tools import yfinance_client as yfwrap
from tools import sec_client
import pandas as pd


def fetch(tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for t in tickers:
        info = yfwrap.info(t)
        hist = yfwrap.history(t, period="6mo")
        # 숫자 필드는 존재 시만 채움
        revenue = info.get("totalRevenue") if isinstance(info.get("totalRevenue"), (int, float)) else None
        ebit = info.get("ebitda") if isinstance(info.get("ebitda"), (int, float)) else None
        fcf = info.get("freeCashflow") if isinstance(info.get("freeCashflow"), (int, float)) else None
        debt = info.get("totalDebt") if isinstance(info.get("totalDebt"), (int, float)) else None

        # asof_date는 가격 히스토리의 최신 인덱스 날짜로 추정
        asof_date = None
        if isinstance(hist, pd.DataFrame) and not hist.empty:
            try:
                asof_date = str(hist.index[-1].date())
            except Exception:
                asof_date = None

        # 출처: 기본 야후 파이낸스, SEC 최근 신고 링크 일부
        sources: List[str] = [f"https://finance.yahoo.com/quote/{t}"]
        filings = sec_client.filings_meta(t)
        if isinstance(filings, list) and filings:
            for f in filings[:3]:
                link = f.get("link")
                if link:
                    sources.append(link)

        results[t] = {
            "revenue": revenue,
            "ebit": ebit,
            "fcf": fcf,
            "debt": debt,
            "asof_date": asof_date,
            "sources": sources,
        }
    return results
