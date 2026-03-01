"""LLM client abstraction (OpenAI/Gemini) with pluggable auth."""

from __future__ import annotations

from typing import Any, Dict
import json
import os
import httpx
from pydantic import BaseModel, Field
from .utils import get_logger, read_json

log = get_logger(__name__)


class AuthConfig(BaseModel):
    method: str = "api_key"
    api_key: str | None = None


class LLMConfig(BaseModel):
    provider: str
    model: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    base_url: str | None = None


def load_llm_config(provider: str, model: str, api_key: str | None = None) -> LLMConfig:
    provider = provider.lower()
    if provider == "openai":
        key = (
            api_key
            or os.getenv("OPENAI_API_KEY")
            or _load_key_store().get("openai_api_key")
        )
        base = os.getenv("OPENAI_BASE_URL")
        return LLMConfig(
            provider=provider, model=model, auth=AuthConfig(api_key=key), base_url=base
        )
    if provider == "gemini":
        key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or _load_key_store().get("gemini_api_key")
        )
        base = os.getenv("GEMINI_BASE_URL")
        return LLMConfig(
            provider=provider, model=model, auth=AuthConfig(api_key=key), base_url=base
        )
    raise ValueError(f"Unsupported provider: {provider}")


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def generate(
        self, system_prompt: str, user_prompt: str, json_mode: bool = True
    ) -> Dict[str, Any]:
        if self.config.provider == "openai":
            return self._openai_chat(system_prompt, user_prompt, json_mode=json_mode)
        if self.config.provider == "gemini":
            return self._gemini_generate(
                system_prompt, user_prompt, json_mode=json_mode
            )
        raise ValueError(f"Unsupported provider: {self.config.provider}")

    def _openai_chat(
        self, system_prompt: str, user_prompt: str, json_mode: bool
    ) -> Dict[str, Any]:
        api_key = self.config.auth.api_key
        if not api_key:
            return {"error": "OPENAI_API_KEY is missing"}
        base_url = self.config.base_url or "https://api.openai.com"
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{base_url}/v1/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning(f"OpenAI 호출 실패: {exc}")
            return {"error": str(exc)}
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _parse_json_or_text(content)

    def _gemini_generate(
        self, system_prompt: str, user_prompt: str, json_mode: bool
    ) -> Dict[str, Any]:
        api_key = self.config.auth.api_key
        if not api_key:
            return {"error": "GEMINI_API_KEY is missing"}
        base_url = self.config.base_url or "https://generativelanguage.googleapis.com"
        payload: Dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        }
        if json_mode:
            payload["generationConfig"] = {"response_mime_type": "application/json"}
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{base_url}/v1beta/models/{self.config.model}:generateContent",
                    params={"key": api_key},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning(f"Gemini 호출 실패: {exc}")
            return {"error": str(exc)}
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
        return _parse_json_or_text(text)


def _load_key_store() -> Dict[str, Any]:
    data = read_json("data/keys.json")
    if isinstance(data, dict):
        return data
    return {}


def _parse_json_or_text(text: str) -> Dict[str, Any]:
    if not text:
        return {"text": ""}
    try:
        return json.loads(text)
    except Exception:
        return {"text": text}
