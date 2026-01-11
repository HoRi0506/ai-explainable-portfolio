from agents.fundamentals_fetcher import fetch


def test_fetch_returns_fields_and_sources():
    res = fetch(["AAPL"])  # 무료 경로(yfinance/SEC 공개 API)
    assert isinstance(res, dict) and "AAPL" in res
    item = res["AAPL"]
    # 필수 키 존재
    for key in ("revenue", "ebit", "fcf", "debt", "asof_date", "sources"):
        assert key in item
    # 출처는 최소 1개(야후 링크)
    assert isinstance(item["sources"], list) and len(item["sources"]) >= 1
