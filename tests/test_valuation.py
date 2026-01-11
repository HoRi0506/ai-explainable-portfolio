from agents.fundamentals_fetcher import fetch
from agents.valuation_analyst import score


def test_valuation_scores_and_rationale():
    facts = fetch(["AAPL", "MSFT"])  # 무료 경로
    out = score(facts)
    assert isinstance(out, list) and len(out) == 2
    ranks = {x["rank"] for x in out}
    assert ranks == {1, 2}
    for x in out:
        assert 0.0 <= x["score"] <= 1.0
        assert isinstance(x["rationale_md"], str) and len(x["rationale_md"]) > 0
