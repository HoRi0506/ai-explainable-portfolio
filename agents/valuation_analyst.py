"""밸류에이션 애널리스트
- 지표: P/E(낮을수록↑), EV/EBITDA(낮을수록↑), FCF Yield(높을수록↑)
- 결측 시 None-safe, 가중 평균으로 (0~1) 점수 산출
"""

from __future__ import annotations
from typing import Dict, Any, List, Tuple
import math
from tools import yfinance_client as yfwrap


def score(facts_by_ticker: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    # 1) 원천 데이터 수집/보강
    metrics: Dict[str, Dict[str, float | None]] = {}
    for t, f in facts_by_ticker.items():
        info = yfwrap.info(t)
        mc = _num(info.get("market_cap"))
        ev = _num(info.get("enterpriseValue"))
        ebitda = _num(info.get("ebitda"))
        fcf = _num(info.get("freeCashflow"))
        pe_trailing = _num(info.get("trailingPE"))
        # EV가 없으면 MarketCap + Debt 근사
        if ev is None and mc is not None:
            debt = _num(f.get("debt")) or _num(info.get("totalDebt"))
            ev = mc + (debt or 0.0)
        # P/E는 우선 trailingPE 사용
        pe = pe_trailing
        # FCF YIELD = FCF / MarketCap
        fcf_yield = (fcf / mc) if (fcf is not None and mc and mc > 0) else None
        ev_ebitda = (ev / ebitda) if (ev and ebitda and ebitda > 0) else None
        metrics[t] = {"pe": pe, "ev_ebitda": ev_ebitda, "fcf_yield": fcf_yield}

    # 2) 정규화 (min-max, 방향성 반영)
    pe_scores = _normalize(metrics, key="pe", higher_is_better=False)
    ev_scores = _normalize(metrics, key="ev_ebitda", higher_is_better=False)
    fcf_scores = _normalize(metrics, key="fcf_yield", higher_is_better=True)

    # 3) 가중 평균
    w_pe, w_ev, w_fcf = 0.33, 0.33, 0.34
    scored: List[Tuple[str, float, str]] = []
    for t in metrics:
        s = (
            (pe_scores.get(t, 0.5) * w_pe)
            + (ev_scores.get(t, 0.5) * w_ev)
            + (fcf_scores.get(t, 0.5) * w_fcf)
        )
        rationale_md = _rationale_md(t, metrics[t], weights=(w_pe, w_ev, w_fcf))
        scored.append((t, float(max(0.0, min(1.0, s))), rationale_md))

    # 4) 정렬 및 랭크
    scored.sort(key=lambda x: x[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for i, (t, s, md) in enumerate(scored, start=1):
        out.append({"ticker": t, "score": s, "rank": i, "rationale_md": md})
    return out


def _num(v) -> float | None:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


def _normalize(metrics: Dict[str, Dict[str, float | None]], key: str, higher_is_better: bool) -> Dict[str, float]:
    vals = [m[key] for m in metrics.values() if m.get(key) is not None]
    if not vals:
        return {t: 0.5 for t in metrics.keys()}
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        return {t: 0.5 for t in metrics.keys()}
    out: Dict[str, float] = {}
    for t, m in metrics.items():
        v = m.get(key)
        if v is None:
            out[t] = 0.5
            continue
        x = (v - vmin) / (vmax - vmin)
        out[t] = float(x if higher_is_better else (1 - x))
    return out


def _rationale_md(ticker: str, m: Dict[str, float | None], weights=(0.33, 0.33, 0.34)) -> str:
    pe = m.get("pe")
    ev = m.get("ev_ebitda")
    fcfy = m.get("fcf_yield")
    return (
        f"- 티커: {ticker}\n"
        f"- P/E: {pe if pe is not None else 'N/A'} (w={weights[0]})\n"
        f"- EV/EBITDA: {ev if ev is not None else 'N/A'} (w={weights[1]})\n"
        f"- FCF Yield: {fcfy if fcfy is not None else 'N/A'} (w={weights[2]})\n"
        "- 수치 출처: yfinance(info/fast_info)"
    )
