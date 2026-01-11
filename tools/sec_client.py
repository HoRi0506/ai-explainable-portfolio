"""SEC EDGAR 래퍼 (간단 버전)
- 최근 10-K/10-Q/Form 4 메타 정보 일부 추출 시도
- 실패 시 빈 리스트로 graceful degrade
"""

from __future__ import annotations
from typing import Dict, Any, List
import os
from .utils import get_logger, request_json

log = get_logger(__name__)


def filings_meta(ticker: str) -> List[Dict[str, Any]]:
    # 최소 구현: SEC Browse-EDGAR의 Atom-ish JSON endpoint가 없어 HTML 파싱이 필요하므로
    # 여기서는 data.sec.gov submissions JSON을 CIK로 조회하는 방식을 시도.
    # CIK 조회 실패/차단 시 빈 리스트 반환.
    try:
        cik = _lookup_cik(ticker)
        if not cik:
            return []
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        headers = _ua_headers()
        j = request_json(url, headers=headers)
        if not isinstance(j, dict):
            return []
        filings = j.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accession = filings.get("accessionNumber", [])
        report_date = filings.get("reportDate", [])
        primary_doc = filings.get("primaryDocument", [])
        out: List[Dict[str, Any]] = []
        for i, form in enumerate(forms):
            if form not in ("10-K", "10-Q", "4"):
                continue
            acc = accession[i] if i < len(accession) else None
            dt = report_date[i] if i < len(report_date) else None
            doc = primary_doc[i] if i < len(primary_doc) else None
            link = f"https://www.sec.gov/ixviewer/doc?action=display&source=content&accno={acc}" if acc else None
            out.append({"form": form, "accession": acc, "date": dt, "link": link, "doc": doc})
        return out
    except Exception as e:
        log.warning(f"SEC filings_meta 실패({ticker}): {e}")
        return []


def _lookup_cik(ticker: str) -> int | None:
    # 회사 목록 JSON을 내려받아 검색 (간단 캐시 생략)
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = _ua_headers()
    try:
        data = request_json(url, headers=headers)
        if not isinstance(data, dict):
            return None
        for _, item in data.items():
            if item.get("ticker", "").upper() == ticker.upper():
                return int(item.get("cik_str"))
        return None
    except Exception as e:
        log.warning(f"CIK 조회 실패({ticker}): {e}")
        return None


def _ua_headers() -> Dict[str, str]:
    email = os.getenv("SEC_API_EMAIL", "anonymous@example.com")
    return {
        "Accept": "application/json",
        "User-Agent": f"ai-explainable-portfolio/0.1 ({email})",
    }
