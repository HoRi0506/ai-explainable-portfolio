from __future__ import annotations

from typing import Dict, Any, List
from datetime import datetime
import os


def render_summary_md(result: Dict[str, Any], template_path: str | None = None) -> str:
    """결과 딕셔너리로 요약 Markdown을 생성한다.

    - 템플릿의 변수: generated_at, sectors, n_stocks, rationale_md, risk_notes, orders_table_md
    - 템플릿 파일이 없으면 기본 템플릿을 사용한다.
    """
    sectors = ", ".join(result.get("sectors", []))
    n_stocks = len(result.get("tickers", []))
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # 종목 선정 사유: valuation.rationale_md를 모아 합치기
    rationales: List[str] = []
    for item in result.get("valuation", []):
        t = item.get("ticker")
        md = item.get("rationale_md") or ""
        rationales.append(f"### {t}\n\n{md}\n")
    rationale_md = "\n".join(rationales) if rationales else "정보 부족"

    # 리스크 요약
    risk_notes = (result.get("risk") or {}).get("notes") or result.get("risk_notes") or "-"

    # 주문 계획 테이블 (Markdown)
    orders = result.get("orders", [])
    headers = ["Ticker", "Qty", "EstPrice", "Fee", "Total"]
    rows = [headers, ["---", "---:", "---:", "---:", "---:"]]
    for o in orders:
        rows.append([
            str(o.get("ticker")),
            str(o.get("qty")),
            _fmt(o.get("est_price")),
            _fmt(o.get("fee")),
            _fmt(o.get("total")),
        ])
    orders_table_md = "\n".join(["| " + " | ".join(r) + " |" for r in rows]) if orders else "-"

    content_vars = {
        "generated_at": generated_at,
        "sectors": sectors,
        "n_stocks": n_stocks,
        "rationale_md": rationale_md,
        "risk_notes": risk_notes,
        "orders_table_md": orders_table_md,
    }

    tpl = _read_template(template_path)
    return _simple_render(tpl, content_vars)


def _fmt(v) -> str:
    try:
        if v is None:
            return "-"
        return f"{float(v):,.2f}"
    except Exception:
        return str(v)


def _read_template(template_path: str | None) -> str:
    candidates = []
    if template_path:
        candidates.append(template_path)
    candidates.append(os.path.join("reports", "templates", "summary.md"))
    for p in candidates:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception:
            continue
    # fallback 템플릿
    return (
        "# 포트폴리오 요약 (기본)\n\n"
        "- 생성일: {{ generated_at }}\n"
        "- 입력 섹터: {{ sectors }}\n"
        "- 종목 수: {{ n_stocks }}\n\n"
        "## 선정 사유 (요약)\n\n{{ rationale_md }}\n\n"
        "## 리스크 요약\n\n{{ risk_notes }}\n\n"
        "## 주문 계획\n\n{{ orders_table_md }}\n"
    )


def _simple_render(template: str, vars: Dict[str, Any]) -> str:
    """아주 단순한 {{ var }} 치환 렌더러 (jinja 미의존)."""
    out = template
    for k, v in vars.items():
        out = out.replace("{{ " + k + " }}", str(v))
    return out

