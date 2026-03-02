from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm
from pydantic import ValidationError

from config.settings import Settings
from engine.strategy_hub import Strategy
from schemas.models import MarketSnapshot, TradeIdea


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "analyst_system.txt"
USER_PROMPT_PATH = PROMPT_DIR / "analyst_user.txt"
logger = logging.getLogger(__name__)


class AnalystAgent:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._tier = settings.litellm.tiers["smart_a"]
        self._model_name = self._tier.model
        self._timeout_sec = self._tier.timeout_sec
        self._system_template = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        self._user_template = USER_PROMPT_PATH.read_text(encoding="utf-8")

    def analyze(self, snapshots: list[MarketSnapshot]) -> list[TradeIdea]:
        if not snapshots:
            return []

        user_prompt = self._user_template.format(
            snapshot_data=self._format_snapshot_data(snapshots),
            current_time=datetime.now(timezone.utc).isoformat(),
        )
        raw = self._call_llm(self._system_template, user_prompt)
        if raw is None:
            return []
        return self._parse_response(raw, snapshots)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                response = litellm.completion(
                    model=self._model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    timeout=self._timeout_sec,
                    num_retries=0,
                )
                # 왜(why): litellm 타입이 CustomStreamWrapper를 포함하므로 런단임 안전 접근 필요
                choices = getattr(response, "choices", None)  # type: ignore[union-attr]
                if not choices or not isinstance(choices, list) or len(choices) == 0:
                    return None
                message = getattr(choices[0], "message", None)  # type: ignore[union-attr]
                content: str | None = getattr(message, "content", None)  # type: ignore[union-attr]
                if content is None:
                    return None
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed
                return None
            except Exception as exc:
                if attempt == max_attempts - 1:
                    logger.warning("LLM 호출 실패 (최종 시도): %s", exc)
                    return None
                logger.debug(
                    "LLM 호출 실패 (시도 %d/%d): %s",
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                time.sleep(2)
        return None

    def _format_snapshot_data(self, snapshots: list[MarketSnapshot]) -> str:
        payload: list[dict[str, Any]] = []
        for snapshot in snapshots:
            candle_data = None
            if snapshot.candle:
                candle_data = snapshot.candle.model_dump()

            features_data = None
            if snapshot.features:
                features_data = snapshot.features.model_dump()

            payload.append(
                {
                    "trace_id": str(snapshot.trace_id),
                    "ts": snapshot.ts.isoformat(),
                    "venue": snapshot.venue.value,
                    "symbol": snapshot.symbol,
                    "price": snapshot.price,
                    "bid": snapshot.bid,
                    "ask": snapshot.ask,
                    "volume": snapshot.volume,
                    "candle": candle_data,
                    "features": features_data,
                }
            )
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _parse_response(
        self,
        raw: dict[str, Any],
        snapshots: list[MarketSnapshot],
    ) -> list[TradeIdea]:
        ideas_raw = raw.get("ideas")
        if not isinstance(ideas_raw, list):
            return []

        latest_snapshot = snapshots[-1]
        latest_by_symbol: dict[str, MarketSnapshot] = {s.symbol: s for s in snapshots}
        ideas: list[TradeIdea] = []

        for item in ideas_raw:
            if not isinstance(item, dict):
                return []

            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)):
                return []

            if confidence < 0.6:
                continue

            symbol = str(item.get("symbol", "")).strip()
            if not symbol:
                return []

            snapshot = latest_by_symbol.get(symbol, latest_snapshot)
            constraints = item.get("constraints")
            merged_constraints: dict[str, Any] = (
                dict(constraints) if isinstance(constraints, dict) else {}
            )
            merged_constraints["venue"] = snapshot.venue.value
            merged_constraints["data_asof"] = snapshot.ts.isoformat()

            candidate = {
                "trace_id": snapshot.trace_id,
                "symbol": symbol,
                "side": item.get("side"),
                "confidence": confidence,
                "horizon": item.get("horizon", "SWING"),
                "thesis": item.get("thesis", ""),
                "entry": item.get("entry"),
                "tp": item.get("tp"),
                "sl": item.get("sl"),
                "constraints": merged_constraints,
            }
            try:
                ideas.append(TradeIdea.model_validate(candidate))
            except ValidationError:
                return []

        return ideas


class AnalystStrategy(Strategy):
    def __init__(self, agent: AnalystAgent):
        self._agent = agent

    def generate(self, snapshots: list[MarketSnapshot]) -> list[TradeIdea]:
        return self._agent.analyze(snapshots)
