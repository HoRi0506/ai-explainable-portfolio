"""인사이더·오너십 애널리스트
- SEC Form 4 메타를 요약하여 최근 이벤트 제공
- 없으면 '최근 Form 4 없음'으로 폴백
"""

from __future__ import annotations
from typing import List, Dict, Any
from tools import sec_client


def summarize(tickers: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in tickers:
        filings = sec_client.filings_meta(t)
        bullets: List[str] = []
        sources: List[str] = []
        if filings:
            for f in filings:
                if f.get("form") == "4":
                    dt = f.get("date")
                    link = f.get("link")
                    text = f"Form 4 최근 신고일: {dt}"
                    if link:
                        text += f" — {link}"
                        sources.append(link)
                    bullets.append(text)
                    if len(bullets) >= 3:
                        break
        if not bullets:
            bullets = ["최근 Form 4 없음"]
        out.append({"ticker": t, "bullets": bullets, "sources": sources})
    return out
