"""트레이드 플래너
- 수수료/슬리피지 반영하여 수량/금액 계산
"""

from __future__ import annotations
from typing import Dict, List, Any
import math
from tools import yfinance_client as yfwrap


def plan(weights: Dict[str, float], budget: float, fee_bps: int = 3, slippage_bps: int = 10) -> List[Dict[str, Any]]:
    orders: List[Dict[str, Any]] = []
    for t, w in weights.items():
        alloc = float(budget) * float(w)
        info = yfwrap.info(t)
        price = info.get("last_price")
        if not price:
            h = yfwrap.history(t, period="5d")
            if h is not None and not getattr(h, "empty", True) and "Close" in h.columns:
                price = float(h["Close"].iloc[-1])
        if not price or price <= 0:
            est_price = None
            qty = 0
            fee = 0.0
            total = 0.0
        else:
            est_price = float(price) * (1.0 + slippage_bps / 10000.0)
            qty = int(math.floor(alloc / est_price))
            gross = qty * est_price
            fee = gross * (fee_bps / 10000.0)
            total = gross + fee
        orders.append({
            "ticker": t,
            "qty": qty,
            "est_price": est_price,
            "fee": fee,
            "total": total,
        })
    return orders
