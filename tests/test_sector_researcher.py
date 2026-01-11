import os
from agents.sector_researcher import research


def test_research_without_key_uses_preset():
    os.environ.pop("FIRECRAWL_API_KEY", None)
    res = research(["AI"], k=5)
    assert isinstance(res, list)
    assert len(res) >= 5
    assert all("ticker" in x and "source_urls" in x for x in res)

