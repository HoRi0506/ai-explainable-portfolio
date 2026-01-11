"""Firecrawl 클라이언트 (간단 버전)
- 검색/스크랩 API 래핑
- 캐시 및 재시도(백오프) 지원

환경 변수
- FIRECRAWL_API_KEY: 필수(없으면 빈 결과)
- FIRECRAWL_BASE_URL: 기본 "https://api.firecrawl.dev"
"""

from __future__ import annotations
from typing import List, Dict, Any
import os
import time
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .utils import get_logger, disk_cache_path, load_cache, save_cache

log = get_logger(__name__)


def _client() -> httpx.Client:
    base = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev")
    key = os.getenv("FIRECRAWL_API_KEY")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=base, headers=headers, timeout=30.0)


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(4),
)
def _get(client: httpx.Client, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    resp = client.get(path, params=params)
    if resp.status_code in (429, 500, 502, 503, 504):
        raise httpx.HTTPStatusError("rate/5xx", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.json()


def search(query: str, k: int = 10) -> List[Dict[str, Any]]:
    cache_key = f"search:{query}:{k}"
    cache_path = disk_cache_path("firecrawl", cache_key)
    cached = load_cache(cache_path)
    if cached is not None:
        return cached

    if not os.getenv("FIRECRAWL_API_KEY"):
        log.warning("FIRECRAWL_API_KEY 미설정 — 빈 결과 반환")
        return []

    with _client() as client:
        try:
            data = _get(client, "/v1/search", {"q": query, "limit": k})
            results = data.get("results") or data.get("data") or []
        except Exception as e:
            log.error(f"Firecrawl search 실패: {e}")
            results = []

    save_cache(cache_path, results)
    return results


def scrape(url: str) -> Dict[str, Any]:
    cache_key = f"scrape:{url}"
    cache_path = disk_cache_path("firecrawl", cache_key)
    cached = load_cache(cache_path)
    if cached is not None:
        return cached

    if not os.getenv("FIRECRAWL_API_KEY"):
        log.warning("FIRECRAWL_API_KEY 미설정 — 빈 결과 반환")
        return {"url": url, "content": None}

    with _client() as client:
        try:
            data = _get(client, "/v1/scrape", {"url": url})
        except Exception as e:
            log.error(f"Firecrawl scrape 실패: {e}")
            data = {"url": url, "content": None}

    save_cache(cache_path, data)
    return data
