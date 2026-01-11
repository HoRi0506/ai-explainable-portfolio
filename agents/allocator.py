"""알로케이터
- 기본: 균등배분
- 제약: 최소/최대 비중 충족(water-filling), 섹터 한도는 추후 구현
"""

from __future__ import annotations
from typing import List, Dict
import math


def allocate(tickers: List[str], constraints: Dict) -> Dict[str, float]:
    if not tickers:
        return {}
    n = len(tickers)
    min_w = float(constraints.get("min_weight", 0.0))
    max_w = float(constraints.get("max_weight", 1.0))
    # 초기 균등
    weights = [1.0 / n for _ in range(n)]
    # water-filling으로 [min,max] 내에서 합=1 달성 시도
    for _ in range(100):
        # clamp
        weights = [min(max(w, min_w), max_w) for w in weights]
        total = sum(weights)
        diff = 1.0 - total
        if abs(diff) < 1e-9:
            break
        if diff > 0:  # 더해야 함
            headrooms = [max_w - w for w in weights]
            head_total = sum(h for h in headrooms if h > 1e-12)
            if head_total <= 1e-12:
                # 불가능: 균등 정규화
                weights = _normalize_within_bounds(weights, min_w, max_w)
                break
            weights = [w + diff * ((max_w - w) / head_total if (max_w - w) > 1e-12 else 0.0) for w in weights]
        else:  # 줄여야 함
            reducibles = [w - min_w for w in weights]
            red_total = sum(r for r in reducibles if r > 1e-12)
            if red_total <= 1e-12:
                weights = _normalize_within_bounds(weights, min_w, max_w)
                break
            weights = [w + diff * ((w - min_w) / red_total if (w - min_w) > 1e-12 else 0.0) for w in weights]
    # 마지막 정리
    total = sum(weights)
    if total != 0:
        weights = [w / total for w in weights]
    return {t: float(w) for t, w in zip(tickers, weights)}


def _normalize_within_bounds(weights, min_w, max_w):
    n = len(weights)
    base = [1.0 / n for _ in range(n)]
    return [min(max(b, min_w), max_w) for b in base]
