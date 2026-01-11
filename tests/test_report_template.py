from reports.render import render_summary_md


def test_render_summary_md_basic():
    sample = {
        "sectors": ["AI"],
        "tickers": ["AAPL"],
        "valuation": [{"ticker": "AAPL", "rationale_md": "- P/E: 20\n- EV/EBITDA: 15\n- FCF Yield: 3%"}],
        "risk": {"notes": "테스트 노트"},
        "orders": [{"ticker": "AAPL", "qty": 10, "est_price": 100.0, "fee": 1.0, "total": 1001.0}],
    }
    md = render_summary_md(sample)
    assert isinstance(md, str) and "포트폴리오 요약" in md
    assert "입력 섹터" in md and "AI" in md
    assert "주문 계획" in md and "AAPL" in md
