from __future__ import annotations

from typing import Dict, Any
from agents.sector_researcher import research
from agents.ticker_screener import screen
from agents.fundamentals_fetcher import fetch
from agents.valuation_analyst import score
from agents.insider_ownership import summarize
from agents.risk_analyst import correlate
from agents.allocator import allocate
from agents.trade_planner import plan
from app.settings import DEFAULT_CONSTRAINTS


def run_demo(input_params: Dict[str, Any]) -> Dict[str, Any]:
    """간단 파이프라인: 리서치→스크리닝→펀더멘털→밸류→리스크→알로케이션→주문.
    아직은 더미/간소화 로직이며 Milestone C에서 보강.
    """
    sectors = input_params.get("sectors", ["AI"]) or ["AI"]
    budget = float(input_params.get("budget", 1_000_000))
    constraints = input_params.get("constraints") or DEFAULT_CONSTRAINTS

    # 1) 리서치
    candidates = research(sectors, k=10)
    # 2) 스크리닝
    screened = screen(candidates)
    tickers = [c["ticker"] for c in screened]
    # 3) 펀더멘털
    facts = fetch(tickers)
    # 4) 밸류에이션 점수
    valuation = score(facts)
    # 5) 인사이더/오너십(요약)
    insider = summarize(tickers)
    # 6) 리스크(상관)
    risk = correlate(tickers)
    # 7) 알로케이션
    weights = allocate(tickers, constraints)
    # 8) 주문 계획
    orders = plan(weights, budget)

    return {
        "sectors": sectors,
        "candidates": candidates,
        "screened": screened,
        "tickers": tickers,
        "facts": facts,
        "valuation": valuation,
        "insider": insider,
        "risk": {"notes": risk.get("risk_notes"), "corr_shape": list(risk.get("corr").shape) if risk.get("corr") is not None else None},
        "weights": weights,
        "orders": orders,
    }
