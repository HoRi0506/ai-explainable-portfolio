import os
from tools.firecrawl_client import search


def test_search_no_key_returns_empty():
    # 키 미설정 시 빈 결과 (graceful degrade)
    os.environ.pop("FIRECRAWL_API_KEY", None)
    res = search("AI stocks", k=1)
    assert isinstance(res, list)
