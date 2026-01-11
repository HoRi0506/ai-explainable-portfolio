from __future__ import annotations

DEFAULT_SECTORS = ["AI", "원자력", "우주", "블록체인"]

DEFAULT_CONSTRAINTS = {
    "min_weight": 0.0,
    "max_weight": 0.35,
    "sector_caps": {},  # e.g., {"AI": 0.7}
    "method": "equal",  # or "risk_parity"
}

FEE_BPS = 3  # 0.03%
SLIPPAGE_BPS = 10  # 0.10%

