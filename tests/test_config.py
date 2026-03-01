"""
tests/test_config.py - 설정 시스템 테스트.

Pydantic 모델 검증, YAML 로드, 기본값, 에러 처리 등을 테스트.
"""

import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError

from config import (
    AppConfig,
    LiteLLMConfig,
    LLMTier,
    MAStrategy,
    RiskPolicyConfig,
    Settings,
    StrategyConfig,
    TimeWindow,
    load_settings,
    load_yaml,
)
from schemas.models import TradingMode, Venue, RiskPolicy


class TestLoadYaml:
    """load_yaml() 함수 테스트."""

    def test_load_yaml_missing_file(self, tmp_path):
        """파일 미존재 시 빈 dict 반환."""
        result = load_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_load_yaml_valid_file(self, tmp_path):
        """유효한 YAML 파일 로드."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("key: value\nnumber: 42\n")
        result = load_yaml(yaml_file)
        assert result == {"key": "value", "number": 42}

    def test_load_yaml_empty_file(self, tmp_path):
        """빈 YAML 파일 로드."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")
        result = load_yaml(yaml_file)
        assert result == {}

    def test_load_yaml_non_dict_content(self, tmp_path):
        """YAML이 dict가 아닌 경우 빈 dict 반환."""
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n")
        result = load_yaml(yaml_file)
        assert result == {}


class TestTimeWindow:
    """TimeWindow 모델 테스트."""

    def test_time_window_valid(self):
        """유효한 시간 창."""
        tw = TimeWindow(start="10:00", end="15:00")
        assert tw.start == "10:00"
        assert tw.end == "15:00"

    def test_time_window_default(self):
        """기본값 사용."""
        tw = TimeWindow(start="08:30", end="16:00")
        assert tw.start == "08:30"
        assert tw.end == "16:00"


class TestAppConfig:
    """AppConfig 모델 테스트."""

    def test_app_config_defaults(self):
        """기본값 검증."""
        app = AppConfig()
        assert app.trading_mode == TradingMode.PAUSED
        assert app.venue == Venue.KR
        assert app.timezone == "Asia/Seoul"
        assert app.max_collections_per_day == 2
        assert app.db_path == "storage/orders.db"
        assert app.log_path == "storage/logs.jsonl"
        assert app.pipeline_timeout_sec == 60
        assert app.data_stale_minutes == 30

    def test_app_config_from_dict(self):
        """dict에서 로드."""
        data = {
            "trading_mode": "PAPER",
            "venue": "US",
            "timezone": "America/New_York",
            "trade_window": {"start": "09:30", "end": "16:00"},
            "data_collection_window": {"start": "08:00", "end": "17:00"},
            "max_collections_per_day": 3,
            "db_path": "custom.db",
            "log_path": "custom.jsonl",
            "pipeline_timeout_sec": 120,
            "data_stale_minutes": 45,
        }
        app = AppConfig(**data)
        assert app.trading_mode == TradingMode.PAPER
        assert app.venue == Venue.US
        assert app.timezone == "America/New_York"
        assert app.max_collections_per_day == 3
        assert app.pipeline_timeout_sec == 120

    def test_app_config_invalid_trading_mode(self):
        """잘못된 trading_mode → ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig(trading_mode="INVALID")

    def test_app_config_invalid_venue(self):
        """잘못된 venue → ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig(venue="JP")

    def test_app_config_invalid_max_collections(self):
        """max_collections_per_day < 1 → ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig(max_collections_per_day=0)

    def test_app_config_invalid_max_collections_too_high(self):
        """max_collections_per_day > 10 → ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig(max_collections_per_day=11)

    def test_app_config_invalid_pipeline_timeout(self):
        """pipeline_timeout_sec < 1 → ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig(pipeline_timeout_sec=0)

    def test_app_config_invalid_data_stale_minutes(self):
        """data_stale_minutes < 1 → ValidationError."""
        with pytest.raises(ValidationError):
            AppConfig(data_stale_minutes=0)


class TestMAStrategy:
    """MAStrategy 모델 테스트."""

    def test_ma_strategy_defaults(self):
        """기본값 검증."""
        strategy = MAStrategy()
        assert strategy.short_window == 5
        assert strategy.long_window == 20
        assert strategy.min_confidence == 0.6

    def test_ma_strategy_custom_values(self):
        """커스텀 값."""
        strategy = MAStrategy(short_window=10, long_window=50, min_confidence=0.7)
        assert strategy.short_window == 10
        assert strategy.long_window == 50
        assert strategy.min_confidence == 0.7

    def test_ma_strategy_invalid_short_window(self):
        """short_window < 1 → ValidationError."""
        with pytest.raises(ValidationError):
            MAStrategy(short_window=0)

    def test_ma_strategy_invalid_long_window(self):
        """long_window < 2 → ValidationError."""
        with pytest.raises(ValidationError):
            MAStrategy(long_window=1)

    def test_ma_strategy_invalid_confidence_too_low(self):
        """min_confidence < 0 → ValidationError."""
        with pytest.raises(ValidationError):
            MAStrategy(min_confidence=-0.1)

    def test_ma_strategy_invalid_confidence_too_high(self):
        """min_confidence > 1 → ValidationError."""
        with pytest.raises(ValidationError):
            MAStrategy(min_confidence=1.1)


class TestStrategyConfig:
    """StrategyConfig 모델 테스트."""

    def test_strategy_config_defaults(self):
        """기본값 검증."""
        config = StrategyConfig()
        assert config.strategies == {}
        assert config.active_strategy == "ma_crossover"

    def test_strategy_config_with_strategies(self):
        """전략 포함."""
        strategies = {
            "ma_crossover": MAStrategy(short_window=5, long_window=20),
            "custom": MAStrategy(short_window=10, long_window=50),
        }
        config = StrategyConfig(strategies=strategies, active_strategy="custom")
        assert len(config.strategies) == 2
        assert config.active_strategy == "custom"

    def test_strategy_config_invalid_active_strategy(self):
        """active_strategy이 strategies에 없음 → ValidationError."""
        strategies = {"ma_crossover": MAStrategy()}
        with pytest.raises(ValidationError):
            StrategyConfig(strategies=strategies, active_strategy="nonexistent")

    def test_strategy_config_empty_strategies_with_default_active(self):
        """strategies가 비어있으면 active_strategy 검증 스킵."""
        config = StrategyConfig(strategies={}, active_strategy="ma_crossover")
        assert config.active_strategy == "ma_crossover"


class TestLLMTier:
    """LLMTier 모델 테스트."""

    def test_llm_tier_minimal(self):
        """최소 필드."""
        tier = LLMTier(model="gpt-4")
        assert tier.model == "gpt-4"
        assert tier.purposes == []
        assert tier.timeout_sec == 30
        assert tier.max_retries == 2

    def test_llm_tier_full(self):
        """모든 필드."""
        tier = LLMTier(
            model="gpt-4",
            purposes=["analysis", "decision"],
            timeout_sec=60,
            max_retries=3,
        )
        assert tier.model == "gpt-4"
        assert tier.purposes == ["analysis", "decision"]
        assert tier.timeout_sec == 60
        assert tier.max_retries == 3

    def test_llm_tier_invalid_timeout(self):
        """timeout_sec < 1 → ValidationError."""
        with pytest.raises(ValidationError):
            LLMTier(model="gpt-4", timeout_sec=0)

    def test_llm_tier_invalid_max_retries(self):
        """max_retries < 0 → ValidationError."""
        with pytest.raises(ValidationError):
            LLMTier(model="gpt-4", max_retries=-1)


class TestLiteLLMConfig:
    """LiteLLMConfig 모델 테스트."""

    def test_litellm_config_defaults(self):
        """기본값 검증."""
        config = LiteLLMConfig()
        assert config.tiers == {}
        assert config.default_tier == "smart"
        assert config.routing == {}

    def test_litellm_config_with_tiers(self):
        """티어 포함."""
        tiers = {
            "quick": LLMTier(model="gemini-flash", purposes=["data_collection"]),
            "smart": LLMTier(model="gpt-4", purposes=["analysis"]),
        }
        config = LiteLLMConfig(tiers=tiers, default_tier="smart")
        assert len(config.tiers) == 2
        assert config.default_tier == "smart"

    def test_litellm_config_with_routing(self):
        """라우팅 규칙 포함."""
        routing = {
            "data_collection": "quick",
            "analysis": "smart",
        }
        config = LiteLLMConfig(routing=routing)
        assert config.routing == routing


class TestRiskPolicy:
    """RiskPolicyConfig 모델 테스트."""

    def test_risk_policy_config_load_from_dict(self):
        """dict에서 로드."""
        data = {
            "profiles": {
                "conservative": {
                    "profile_name": "conservative",
                    "max_position_pct": 3.0,
                    "max_positions": 3,
                    "max_drawdown_pct": 5.0,
                    "daily_loss_limit_pct": 1.0,
                    "max_daily_orders": 3,
                    "market_open_delay_minutes": 60,
                    "data_collection_start": "08:30",
                    "data_collection_end": "15:00",
                    "trading_start": "10:00",
                    "trading_end": "15:00",
                },
                "defensive": {
                    "profile_name": "defensive",
                    "max_position_pct": 5.0,
                    "max_positions": 5,
                    "max_drawdown_pct": 7.0,
                    "daily_loss_limit_pct": 1.5,
                    "max_daily_orders": 5,
                    "market_open_delay_minutes": 60,
                    "data_collection_start": "08:30",
                    "data_collection_end": "15:00",
                    "trading_start": "10:00",
                    "trading_end": "15:00",
                },
            },
            "active_profile": "defensive",
        }
        config = RiskPolicyConfig(**data)
        assert len(config.profiles) == 2
        assert config.active_profile == "defensive"

    def test_risk_policy_get_active(self):
        """get_active() 메서드."""
        data = {
            "profiles": {
                "conservative": {
                    "profile_name": "conservative",
                    "max_position_pct": 3.0,
                    "max_positions": 3,
                    "max_drawdown_pct": 5.0,
                    "daily_loss_limit_pct": 1.0,
                    "max_daily_orders": 3,
                    "market_open_delay_minutes": 60,
                    "data_collection_start": "08:30",
                    "data_collection_end": "15:00",
                    "trading_start": "10:00",
                    "trading_end": "15:00",
                }
            },
            "active_profile": "conservative",
        }
        config = RiskPolicyConfig(**data)
        active = config.get_active()
        assert active.profile_name == "conservative"
        assert active.max_position_pct == 3.0

    def test_risk_policy_invalid_active_profile(self):
        """active_profile이 profiles에 없음 → ValidationError."""
        data = {
            "profiles": {
                "conservative": {
                    "profile_name": "conservative",
                    "max_position_pct": 3.0,
                    "max_positions": 3,
                    "max_drawdown_pct": 5.0,
                    "daily_loss_limit_pct": 1.0,
                    "max_daily_orders": 3,
                    "market_open_delay_minutes": 60,
                    "data_collection_start": "08:30",
                    "data_collection_end": "15:00",
                    "trading_start": "10:00",
                    "trading_end": "15:00",
                }
            },
            "active_profile": "nonexistent",
        }
        with pytest.raises(ValidationError):
            RiskPolicyConfig(**data)



class TestSettings:
    """Settings 통합 모델 테스트."""

    def test_settings_minimal(self):
        """최소 필드 (risk만 필수)."""
        risk_data = {
            "profiles": {
                "defensive": {
                    "profile_name": "defensive",
                    "max_position_pct": 5.0,
                    "max_positions": 5,
                    "max_drawdown_pct": 7.0,
                    "daily_loss_limit_pct": 1.5,
                    "max_daily_orders": 5,
                    "market_open_delay_minutes": 60,
                    "data_collection_start": "08:30",
                    "data_collection_end": "15:00",
                    "trading_start": "10:00",
                    "trading_end": "15:00",
                }
            },
            "active_profile": "defensive",
        }
        settings = Settings(risk=RiskPolicyConfig(**risk_data))
        assert settings.app.trading_mode == TradingMode.PAUSED
        assert settings.risk.active_profile == "defensive"

    def test_settings_full(self):
        """모든 필드."""
        risk_data = {
            "profiles": {
                "defensive": {
                    "profile_name": "defensive",
                    "max_position_pct": 5.0,
                    "max_positions": 5,
                    "max_drawdown_pct": 7.0,
                    "daily_loss_limit_pct": 1.5,
                    "max_daily_orders": 5,
                    "market_open_delay_minutes": 60,
                    "data_collection_start": "08:30",
                    "data_collection_end": "15:00",
                    "trading_start": "10:00",
                    "trading_end": "15:00",
                }
            },
            "active_profile": "defensive",
        }
        app_config = AppConfig(trading_mode="PAPER")
        strategy_config = StrategyConfig(
            strategies={"ma_crossover": MAStrategy()},
            active_strategy="ma_crossover",
        )
        litellm_config = LiteLLMConfig(
            tiers={"smart": LLMTier(model="gpt-4")},
            default_tier="smart",
        )
        settings = Settings(
            app=app_config,
            risk=RiskPolicyConfig(**risk_data),
            strategy=strategy_config,
            litellm=litellm_config,
        )
        assert settings.app.trading_mode == TradingMode.PAPER
        assert settings.risk.active_profile == "defensive"
        assert settings.strategy.active_strategy == "ma_crossover"
        assert settings.litellm.default_tier == "smart"


class TestLoadSettings:
    """load_settings() 함수 통합 테스트."""

    def test_load_settings_from_actual_config_dir(self):
        """실제 config/ 디렉토리에서 로드."""
        settings = load_settings("config")
        assert settings.app.trading_mode == TradingMode.PAUSED
        assert settings.app.venue == Venue.KR
        assert settings.risk.active_profile == "defensive"
        assert settings.strategy.active_strategy == "ma_crossover"
        assert settings.litellm.default_tier == "smart_a"

    def test_load_settings_from_temp_dir(self, tmp_path):
        """임시 디렉토리에서 로드."""
        # app.yaml
        app_yaml = tmp_path / "app.yaml"
        app_yaml.write_text(
            """
trading_mode: PAPER
venue: US
timezone: America/New_York
trade_window:
  start: "09:30"
  end: "16:00"
data_collection_window:
  start: "08:00"
  end: "17:00"
max_collections_per_day: 3
db_path: custom.db
log_path: custom.jsonl
pipeline_timeout_sec: 120
data_stale_minutes: 45
"""
        )

        # risk_policy.yaml
        risk_yaml = tmp_path / "risk_policy.yaml"
        risk_yaml.write_text(
            """
profiles:
  conservative:
    profile_name: conservative
    max_position_pct: 3.0
    max_positions: 3
    max_drawdown_pct: 5.0
    daily_loss_limit_pct: 1.0
    max_daily_orders: 3
    market_open_delay_minutes: 60
    data_collection_start: "08:30"
    data_collection_end: "15:00"
    trading_start: "10:00"
    trading_end: "15:00"
active_profile: conservative
"""
        )

        # strategy.yaml
        strategy_yaml = tmp_path / "strategy.yaml"
        strategy_yaml.write_text(
            """
strategies:
  ma_crossover:
    short_window: 5
    long_window: 20
    min_confidence: 0.6
active_strategy: ma_crossover
"""
        )

        # litellm_config.yaml
        litellm_yaml = tmp_path / "litellm_config.yaml"
        litellm_yaml.write_text(
            """
tiers:
  quick:
    model: gemini-flash
    purposes:
      - data_collection
    timeout_sec: 30
    max_retries: 2
  smart:
    model: gpt-4
    purposes:
      - analysis
    timeout_sec: 30
    max_retries: 2
default_tier: smart
routing:
  data_collection: quick
  analysis: smart
"""
        )

        settings = load_settings(tmp_path)
        assert settings.app.trading_mode == TradingMode.PAPER
        assert settings.app.venue == Venue.US
        assert settings.app.max_collections_per_day == 3
        assert settings.risk.active_profile == "conservative"
        assert settings.strategy.active_strategy == "ma_crossover"
        assert settings.litellm.default_tier == "smart"

    def test_load_settings_missing_risk_yaml(self, tmp_path):
        """risk_policy.yaml 미존재 → ValidationError (필수)."""
        # 다른 파일들은 생성하지만 risk_policy.yaml은 생성하지 않음
        app_yaml = tmp_path / "app.yaml"
        app_yaml.write_text("trading_mode: PAUSED\n")

        with pytest.raises(ValidationError):
            load_settings(tmp_path)


    def test_load_settings_roundtrip(self):
        """Settings 직렬화/역직렬화."""
        settings = load_settings("config")
        # dict로 변환
        settings_dict = settings.model_dump()
        # 다시 로드
        settings2 = Settings(**settings_dict)
        assert settings2.app.trading_mode == settings.app.trading_mode
        assert settings2.risk.active_profile == settings.risk.active_profile


class TestRiskPolicyValues:
    """실제 risk_policy.yaml의 값 검증."""

    def test_risk_policy_conservative_values(self):
        """conservative 프로필 값 검증."""
        settings = load_settings("config")
        conservative = settings.risk.profiles["conservative"]
        assert conservative.max_position_pct == 3.0
        assert conservative.max_positions == 3
        assert conservative.max_drawdown_pct == 5.0
        assert conservative.daily_loss_limit_pct == 1.0
        assert conservative.max_daily_orders == 3

    def test_risk_policy_defensive_values(self):
        """defensive 프로필 값 검증."""
        settings = load_settings("config")
        defensive = settings.risk.profiles["defensive"]
        assert defensive.max_position_pct == 5.0
        assert defensive.max_positions == 5
        assert defensive.max_drawdown_pct == 7.0
        assert defensive.daily_loss_limit_pct == 1.5
        assert defensive.max_daily_orders == 5

    def test_risk_policy_aggressive_values(self):
        """aggressive 프로필 값 검증."""
        settings = load_settings("config")
        aggressive = settings.risk.profiles["aggressive"]
        assert aggressive.max_position_pct == 10.0
        assert aggressive.max_positions == 8
        assert aggressive.max_drawdown_pct == 10.0
        assert aggressive.daily_loss_limit_pct == 2.0
        assert aggressive.max_daily_orders == 10


class TestStrategyValues:
    """실제 strategy.yaml의 값 검증."""

    def test_strategy_ma_crossover_values(self):
        """ma_crossover 전략 값 검증."""
        settings = load_settings("config")
        ma = settings.strategy.strategies["ma_crossover"]
        assert ma.short_window == 5
        assert ma.long_window == 20
        assert ma.min_confidence == 0.6


class TestLiteLLMValues:
    """실제 litellm_config.yaml의 값 검증."""

    def test_litellm_quick_tier(self):
        """quick 티어 검증."""
        settings = load_settings("config")
        quick = settings.litellm.tiers["quick"]
        assert quick.model == "gemini/gemini-2.5-flash"
        assert "data_collection" in quick.purposes
        assert "monitoring_text" in quick.purposes
        assert quick.timeout_sec == 30
        assert quick.max_retries == 2

    def test_litellm_smart_a_tier(self):
        """smart_a 티어 검증."""
        settings = load_settings("config")
        smart_a = settings.litellm.tiers["smart_a"]
        assert smart_a.model == "anthropic/claude-haiku-4-5-20250514"
        assert "trade_analysis" in smart_a.purposes
        assert "data_synthesis" in smart_a.purposes
        assert smart_a.timeout_sec == 30
        assert smart_a.max_retries == 2

    def test_litellm_smart_b_tier(self):
        """smart_b 티어 검증."""
        settings = load_settings("config")
        smart_b = settings.litellm.tiers["smart_b"]
        assert smart_b.model == "openai/gpt-5.2"
        assert "trade_analysis_verification" in smart_b.purposes
        assert "cross_check" in smart_b.purposes
        assert smart_b.timeout_sec == 30
        assert smart_b.max_retries == 2

    def test_litellm_expert_tier(self):
        """expert 티어 검증."""
        settings = load_settings("config")
        expert = settings.litellm.tiers["expert"]
        assert expert.model == "anthropic/claude-opus-4-5-20250514"
        assert "buy_sell_decision" in expert.purposes
        assert "high_uncertainty" in expert.purposes
        assert expert.timeout_sec == 60
        assert expert.max_retries == 2

    def test_litellm_routing(self):
        """라우팅 규칙 검증."""
        settings = load_settings("config")
        assert settings.litellm.routing["data_collection"] == "quick"
        assert settings.litellm.routing["monitoring_text"] == "quick"
        assert settings.litellm.routing["trade_analysis"] == "smart_a"
        assert settings.litellm.routing["trade_analysis_verification"] == "smart_b"
        assert settings.litellm.routing["buy_sell_decision"] == "expert"
