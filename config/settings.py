"""
config/settings.py - 설정 시스템.

YAML 파일에서 설정을 로드하고 Pydantic으로 검증.
시크릿(.env)은 별도 관리 - 이 모듈은 YAML 설정만 담당.
"""

import yaml
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field, field_validator
from schemas.models import TradingMode, Venue, RiskPolicy

# --- Sub-models ---


class TimeWindow(BaseModel):
    """시간 창 (KST HH:MM 형식)."""

    start: str  # "HH:MM"
    end: str  # "HH:MM"


class AppConfig(BaseModel):
    """앱 기본 설정 (app.yaml)."""

    trading_mode: TradingMode = TradingMode.PAUSED
    venue: Venue = Venue.KR
    timezone: str = "Asia/Seoul"
    trade_window: TimeWindow = Field(
        default_factory=lambda: TimeWindow(start="10:00", end="15:00")
    )
    data_collection_window: TimeWindow = Field(
        default_factory=lambda: TimeWindow(start="08:30", end="15:00")
    )
    max_collections_per_day: int = Field(default=2, ge=1, le=10)
    db_path: str = "storage/orders.db"
    log_path: str = "storage/logs.jsonl"
    pipeline_timeout_sec: int = Field(default=60, ge=1)
    data_stale_minutes: int = Field(default=30, ge=1)


class RiskPolicyConfig(BaseModel):
    """리스크 정책 설정 (risk_policy.yaml)."""

    profiles: dict[str, RiskPolicy]  # profile_name -> RiskPolicy
    active_profile: str = "defensive"

    @field_validator("active_profile")
    @classmethod
    def validate_active_profile(cls, v, info):
        """active_profile이 profiles에 존재하는지 검증."""
        profiles = info.data.get("profiles", {})
        if profiles and v not in profiles:
            raise ValueError(
                f"active_profile '{v}' not found in profiles: {list(profiles.keys())}"
            )
        return v

    def get_active(self) -> RiskPolicy:
        """현재 활성 리스크 정책 반환."""
        return self.profiles[self.active_profile]


class MAStrategy(BaseModel):
    """이동평균 크로스오버 전략 파라미터."""

    short_window: int = Field(default=5, ge=1)
    long_window: int = Field(default=20, ge=2)
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class StrategyConfig(BaseModel):
    """전략 설정 (strategy.yaml)."""

    strategies: dict[str, MAStrategy] = Field(default_factory=dict)
    active_strategy: str = "ma_crossover"

    @field_validator("active_strategy")
    @classmethod
    def validate_active_strategy(cls, v, info):
        """active_strategy이 strategies에 존재하는지 검증."""
        strategies = info.data.get("strategies", {})
        if strategies and v not in strategies:
            raise ValueError(
                f"active_strategy '{v}' not found in strategies: {list(strategies.keys())}"
            )
        return v


class LLMTier(BaseModel):
    """LLM 티어 설정."""

    model: str
    purposes: list[str] = []
    timeout_sec: int = Field(default=30, ge=1)
    max_retries: int = Field(default=2, ge=0)


class LiteLLMConfig(BaseModel):
    """LiteLLM 모델 라우팅 설정 (litellm_config.yaml)."""

    tiers: dict[str, LLMTier] = Field(default_factory=dict)
    default_tier: str = "smart"
    routing: dict[str, str] = Field(default_factory=dict)  # purpose -> tier name


class Settings(BaseModel):
    """통합 설정. 4개 YAML에서 로드."""

    app: AppConfig = Field(default_factory=AppConfig)
    risk: RiskPolicyConfig
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)


def load_yaml(path: Path) -> dict[str, Any]:
    """YAML 파일 로드. 파일 미존재 시 빈 dict 반환."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def load_settings(config_dir: str | Path = "config") -> Settings:
    """config/ 디렉토리에서 4개 YAML 로드 -> Settings 반환.

    잘못된 값 -> Pydantic ValidationError.
    """
    config_path = Path(config_dir)

    app_data = load_yaml(config_path / "app.yaml")
    risk_data = load_yaml(config_path / "risk_policy.yaml")
    strategy_data = load_yaml(config_path / "strategy.yaml")
    litellm_data = load_yaml(config_path / "litellm_config.yaml")

    return Settings(
        app=AppConfig(**app_data) if app_data else AppConfig(),
        risk=RiskPolicyConfig(**risk_data),
        strategy=StrategyConfig(**strategy_data) if strategy_data else StrategyConfig(),
        litellm=LiteLLMConfig(**litellm_data) if litellm_data else LiteLLMConfig(),
    )
