from agents.insider_ownership import summarize


def test_summarize_returns_bullets():
    res = summarize(["AAPL"])  # 공개 API 경로
    assert isinstance(res, list) and len(res) == 1
    item = res[0]
    assert item["ticker"] == "AAPL"
    assert isinstance(item["bullets"], list) and len(item["bullets"]) >= 1
