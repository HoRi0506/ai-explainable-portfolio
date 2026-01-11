from agents.valuation_analyst import _normalize


def test_normalize_all_equal_returns_half():
    metrics = {
        "A": {"pe": 10.0, "ev_ebitda": 5.0, "fcf_yield": 0.02},
        "B": {"pe": 10.0, "ev_ebitda": 5.0, "fcf_yield": 0.02},
    }
    pe = _normalize(metrics, key="pe", higher_is_better=False)
    ev = _normalize(metrics, key="ev_ebitda", higher_is_better=False)
    fcf = _normalize(metrics, key="fcf_yield", higher_is_better=True)
    assert set(pe.values()) == {0.5}
    assert set(ev.values()) == {0.5}
    assert set(fcf.values()) == {0.5}

