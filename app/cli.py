from __future__ import annotations

import json
import argparse
from typing import Any, Dict
from app.graph import run_demo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=1_000_000)
    ap.add_argument("--sectors", nargs="*", default=["AI"]) 
    args = ap.parse_args()

    payload: Dict[str, Any] = run_demo({"budget": args.budget, "sectors": args.sectors})
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()

