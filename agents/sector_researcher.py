"""섹터 리서처
- 가능한 경우 Firecrawl 검색 사용
- 유료 키 미설정/오류 시 무료 프리셋 폴백
"""

from __future__ import annotations
from typing import List, Dict, Any
import os
from tools import firecrawl_client


def research(sectors: List[str], k: int = 10) -> List[Dict[str, Any]]:
    # 1) Firecrawl 키가 있으면 간단 검색 시도(티커 추출은 한계가 있어 URL만 보조로 사용)
    api_key = os.getenv("FIRECRAWL_API_KEY")
    candidates: List[Dict[str, Any]] = []
    if api_key:
        queries = [f"{s} stocks top companies ticker list" for s in sectors]
        urls: List[str] = []
        for q in queries:
            res = firecrawl_client.search(q, k=max(5, k))
            for item in res:
                u = item.get("url") or item.get("link")
                if u:
                    urls.append(u)
        # Firecrawl만으로 티커 식별이 어려우므로, 프리셋과 결합하여 소스 URL을 부여
        base = _preset_candidates(sectors, size=k)
        if urls:
            for c in base:
                c.setdefault("source_urls", [])
                c["source_urls"].extend(urls[:3])
        candidates = base[:k]
    else:
        # 2) 키가 없으면 프리셋 폴백 (무료)
        candidates = _preset_candidates(sectors, size=k)

    return candidates[:k]


def _preset_candidates(sectors: List[str], size: int = 10) -> List[Dict[str, Any]]:
    presets: Dict[str, List[Dict[str, Any]]]= {
        "AI": [
            {"ticker": "AAPL", "name": "Apple", "source_urls": ["https://www.apple.com"]},
            {"ticker": "MSFT", "name": "Microsoft", "source_urls": ["https://www.microsoft.com"]},
            {"ticker": "NVDA", "name": "NVIDIA", "source_urls": ["https://www.nvidia.com"]},
            {"ticker": "GOOGL", "name": "Alphabet", "source_urls": ["https://abc.xyz"]},
            {"ticker": "AMZN", "name": "Amazon", "source_urls": ["https://www.aboutamazon.com"]},
        ],
        "원자력": [
            {"ticker": "CCJ", "name": "Cameco", "source_urls": ["https://www.cameco.com"]},
            {"ticker": "SMR", "name": "NuScale Power", "source_urls": ["https://www.nuscalepower.com"]},
            {"ticker": "BWXT", "name": "BWX Technologies", "source_urls": ["https://www.bwxt.com"]},
            {"ticker": "UEC", "name": "Uranium Energy", "source_urls": ["https://www.uraniumenergy.com"]},
            {"ticker": "NRG", "name": "NRG Energy", "source_urls": ["https://www.nrg.com"]},
        ],
        "우주": [
            {"ticker": "SPCE", "name": "Virgin Galactic", "source_urls": ["https://www.virgin.com"]},
            {"ticker": "LMT", "name": "Lockheed Martin", "source_urls": ["https://www.lockheedmartin.com"]},
            {"ticker": "BA", "name": "Boeing", "source_urls": ["https://www.boeing.com"]},
            {"ticker": "RTX", "name": "RTX", "source_urls": ["https://www.rtx.com"]},
            {"ticker": "NOC", "name": "Northrop Grumman", "source_urls": ["https://www.northropgrumman.com"]},
        ],
        "블록체인": [
            {"ticker": "COIN", "name": "Coinbase", "source_urls": ["https://www.coinbase.com"]},
            {"ticker": "RIOT", "name": "Riot Platforms", "source_urls": ["https://www.riotplatforms.com"]},
            {"ticker": "MARA", "name": "Marathon Digital", "source_urls": ["https://www.mara.com"]},
            {"ticker": "MSTR", "name": "MicroStrategy", "source_urls": ["https://www.microstrategy.com"]},
            {"ticker": "HIVE", "name": "HIVE Blockchain", "source_urls": ["https://hive.com"]},
        ],
    }
    out: List[Dict[str, Any]] = []
    for s in sectors:
        out.extend(presets.get(s, []))
    # 중복 제거
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for c in out:
        t = c.get("ticker")
        if t and t not in seen:
            seen.add(t)
            uniq.append(c)
    return uniq[:size]
