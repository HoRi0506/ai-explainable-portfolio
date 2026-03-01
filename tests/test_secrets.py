"""
tests/test_secrets.py - 시크릿 관리 및 로그 레닥션 테스트.

30개 테스트 케이스:
1-14: redact_secrets 함수 테스트
15-18: _is_sensitive_key 함수 테스트
19-25: SecretManager 클래스 테스트
26-27: _warn_env_permissions 함수 테스트
28-30: AuditLogger 통합 테스트
"""

import json
import os
import stat
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel, Field

from config.secrets import (  # type: ignore[attr-defined]
    SERVICE_NAME,
    SecretManager,
    _REDACT_MARKER,
    _is_sensitive_key,
    _redact_value,
    redact_secrets,
)
from engine.logger import AuditLogger


# ============================================================================
# Test Models
# ============================================================================


class SimpleEvent(BaseModel):
    """간단한 테스트 이벤트."""

    message: str
    value: int


class EventWithSecrets(BaseModel):
    """민감 데이터를 포함한 테스트 이벤트."""

    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    api_key: str = "sk-test-1234567890abcdefghij"
    app_secret: str = "secret-value-123"
    access_token: str = "token-xyz"
    symbol: str = "AAPL"
    side: str = "BUY"
    confidence: float = 0.85


# ============================================================================
# redact_secrets Tests
# ============================================================================


class TestRedactSecrets:
    """redact_secrets 함수 테스트."""

    def test_redact_simple_keys(self) -> None:
        """api_key, app_secret, access_token → redacted."""
        data = {
            "api_key": "sk-test-1234567890abcdefghij",
            "app_secret": "secret-value-123",
            "access_token": "token-xyz",
        }
        result = redact_secrets(data)

        assert result["api_key"] == _REDACT_MARKER
        assert result["app_secret"] == _REDACT_MARKER
        assert result["access_token"] == _REDACT_MARKER

    def test_redact_case_insensitive(self) -> None:
        """API_KEY, App_Secret → redacted (case-insensitive)."""
        data = {
            "API_KEY": "sk-test-1234567890abcdefghij",
            "App_Secret": "secret-value-123",
            "ACCESS_TOKEN": "token-xyz",
        }
        result = redact_secrets(data)

        assert result["API_KEY"] == _REDACT_MARKER
        assert result["App_Secret"] == _REDACT_MARKER
        assert result["ACCESS_TOKEN"] == _REDACT_MARKER

    def test_redact_hyphenated_keys(self) -> None:
        """APCA-API-SECRET-KEY → redacted."""
        data = {
            "APCA-API-SECRET-KEY": "secret-value-123",
            "KIS-APP-KEY": "key-value-456",
        }
        result = redact_secrets(data)

        assert result["APCA-API-SECRET-KEY"] == _REDACT_MARKER
        assert result["KIS-APP-KEY"] == _REDACT_MARKER

    def test_redact_nested_dict(self) -> None:
        """Deeply nested structures → redacted."""
        data = {
            "user": {
                "name": "Alice",
                "credentials": {
                    "api_key": "sk-test-1234567890abcdefghij",
                    "password": "secret-pass-123",
                },
            },
            "symbol": "AAPL",
        }
        result = redact_secrets(data)

        assert result["user"]["credentials"]["api_key"] == _REDACT_MARKER
        assert result["user"]["credentials"]["password"] == _REDACT_MARKER
        assert result["user"]["name"] == "Alice"
        assert result["symbol"] == "AAPL"

    def test_redact_list_of_dicts(self) -> None:
        """List containing dicts with secrets → redacted."""
        data = {
            "orders": [
                {"symbol": "AAPL", "api_key": "sk-test-1234567890abcdefghij"},
                {"symbol": "GOOGL", "api_key": "sk-test-9876543210zyxwvutsrq"},
            ]
        }
        result = redact_secrets(data)

        assert result["orders"][0]["api_key"] == _REDACT_MARKER
        assert result["orders"][1]["api_key"] == _REDACT_MARKER
        assert result["orders"][0]["symbol"] == "AAPL"
        assert result["orders"][1]["symbol"] == "GOOGL"

    def test_redact_original_immutable(self) -> None:
        """Original dict unchanged after redaction."""
        original = {
            "api_key": "sk-test-1234567890abcdefghij",
            "symbol": "AAPL",
        }
        original_copy = original.copy()

        result = redact_secrets(original)

        # Original should be unchanged
        assert original == original_copy
        assert original["api_key"] == "sk-test-1234567890abcdefghij"
        # Result should be redacted
        assert result["api_key"] == _REDACT_MARKER

    def test_redact_non_secret_keys_untouched(self) -> None:
        """symbol, side, confidence → not redacted."""
        data = {
            "symbol": "AAPL",
            "side": "BUY",
            "confidence": 0.85,
            "trace_id": "abc-123",
            "entry": 150.0,
            "exit": 155.0,
        }
        result = redact_secrets(data)

        assert result["symbol"] == "AAPL"
        assert result["side"] == "BUY"
        assert result["confidence"] == 0.85
        assert result["trace_id"] == "abc-123"
        assert result["entry"] == 150.0
        assert result["exit"] == 155.0

    def test_redact_none_values(self) -> None:
        """None values for secret keys → not redacted (None is safe)."""
        data = {
            "api_key": None,
            "app_secret": None,
            "symbol": None,
        }
        result = redact_secrets(data)

        # None values are not redacted since they don't contain secrets
        assert result["api_key"] is None
        assert result["app_secret"] is None
        assert result["symbol"] is None

    def test_redact_exact_keys(self) -> None:
        """authorization, passphrase, signature → redacted."""
        data = {
            "authorization": "Bearer eyJ...",
            "passphrase": "secret-passphrase",
            "signature": "sig-value-123",
        }
        result = redact_secrets(data)

        assert result["authorization"] == _REDACT_MARKER
        assert result["passphrase"] == _REDACT_MARKER
        assert result["signature"] == _REDACT_MARKER

    def test_redact_value_pattern_sk(self) -> None:
        """String containing 'sk-abc123...' → masked."""
        data = {
            "description": "Using key sk-1234567890abcdefghij for auth",
            "symbol": "AAPL",
        }
        result = redact_secrets(data)

        assert "sk-1234567890abcdefghij" not in result["description"]
        assert _REDACT_MARKER in result["description"]
        assert result["symbol"] == "AAPL"

    def test_redact_value_pattern_bearer(self) -> None:
        """'Bearer eyJ...' → masked."""
        data = {
            "header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "symbol": "AAPL",
        }
        result = redact_secrets(data)

        assert "Bearer" not in result["header"] or _REDACT_MARKER in result["header"]
        assert result["symbol"] == "AAPL"

    def test_redact_value_pattern_jwt(self) -> None:
        """JWT token in non-secret field → masked."""
        data = {
            "metadata": "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "symbol": "AAPL",
        }
        result = redact_secrets(data)

        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result["metadata"]
        assert _REDACT_MARKER in result["metadata"]
        assert result["symbol"] == "AAPL"

    def test_redact_empty_dict(self) -> None:
        """{} → {}."""
        data = {}
        result = redact_secrets(data)

        assert result == {}

    def test_redact_no_secrets(self) -> None:
        """Dict with no secrets → unchanged."""
        data = {
            "symbol": "AAPL",
            "side": "BUY",
            "confidence": 0.85,
            "entry": 150.0,
            "exit": 155.0,
        }
        result = redact_secrets(data)

        assert result == data


# ============================================================================
# _is_sensitive_key Tests
# ============================================================================


class TestIsSensitiveKey:
    """_is_sensitive_key 함수 테스트."""

    def test_sensitive_key_suffixes(self) -> None:
        """key, secret, token, password → sensitive."""
        assert _is_sensitive_key("api_key") is True
        assert _is_sensitive_key("app_secret") is True
        assert _is_sensitive_key("access_token") is True
        assert _is_sensitive_key("master_password") is True

    def test_sensitive_key_case_insensitive(self) -> None:
        """API_KEY, App_Secret → sensitive (case-insensitive)."""
        assert _is_sensitive_key("API_KEY") is True
        assert _is_sensitive_key("App_Secret") is True
        assert _is_sensitive_key("ACCESS_TOKEN") is True

    def test_sensitive_key_hyphenated(self) -> None:
        """APCA-API-SECRET-KEY → sensitive."""
        assert _is_sensitive_key("APCA-API-SECRET-KEY") is True
        assert _is_sensitive_key("KIS-APP-KEY") is True

    def test_sensitive_key_exact(self) -> None:
        """authorization, passphrase, signature → sensitive."""
        assert _is_sensitive_key("authorization") is True
        assert _is_sensitive_key("passphrase") is True
        assert _is_sensitive_key("signature") is True

    def test_non_sensitive_keys(self) -> None:
        """symbol, side, confidence, trace_id → not sensitive."""
        assert _is_sensitive_key("symbol") is False
        assert _is_sensitive_key("side") is False
        assert _is_sensitive_key("confidence") is False
        assert _is_sensitive_key("trace_id") is False
        assert _is_sensitive_key("entry") is False
        assert _is_sensitive_key("exit") is False


# ============================================================================
# _redact_value Tests
# ============================================================================


class TestRedactValue:
    """_redact_value 함수 테스트."""

    def test_redact_value_sk_pattern(self) -> None:
        """sk-abc123... → masked."""
        value = "Using key sk-1234567890abcdefghij for auth"
        result = _redact_value(value)

        assert "sk-1234567890abcdefghij" not in result
        assert _REDACT_MARKER in result

    def test_redact_value_bearer_pattern(self) -> None:
        """Bearer token → masked."""
        value = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = _redact_value(value)

        assert "Bearer" not in result or _REDACT_MARKER in result

    def test_redact_value_jwt_pattern(self) -> None:
        """JWT token → masked."""
        value = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = _redact_value(value)

        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert _REDACT_MARKER in result

    def test_redact_value_no_pattern(self) -> None:
        """No pattern match → unchanged."""
        value = "This is a normal string with no secrets"
        result = _redact_value(value)

        assert result == value


# ============================================================================
# SecretManager Tests
# ============================================================================


class TestSecretManager:
    """SecretManager 클래스 테스트."""

    def test_secret_manager_init(self) -> None:
        """SecretManager 초기화."""
        manager = SecretManager()
        assert manager._service == SERVICE_NAME

    def test_secret_manager_custom_service(self) -> None:
        """Custom service name."""
        manager = SecretManager(service_name="custom-service")
        assert manager._service == "custom-service"

    @patch("keyring.get_password")
    def test_get_from_keyring(self, mock_get: MagicMock) -> None:
        """keyring.get_password returns value."""
        mock_get.return_value = "secret-value-123"
        manager = SecretManager()

        result = manager.get("TEST_KEY")

        assert result == "secret-value-123"
        mock_get.assert_called_once_with(SERVICE_NAME, "TEST_KEY")

    @patch("keyring.get_password")
    def test_get_fallback_to_env(self, mock_get: MagicMock) -> None:
        """keyring returns None, env var set → returns env."""
        mock_get.return_value = None
        manager = SecretManager()

        with patch.dict(os.environ, {"TEST_KEY": "env-value-456"}):
            result = manager.get("TEST_KEY")

        assert result == "env-value-456"

    @patch("keyring.get_password")
    def test_get_returns_none(self, mock_get: MagicMock) -> None:
        """Neither keyring nor env → None."""
        mock_get.return_value = None
        manager = SecretManager()

        with patch.dict(os.environ, {}, clear=True):
            result = manager.get("NONEXISTENT_KEY")

        assert result is None

    @patch("keyring.get_password")
    def test_get_keyring_error_fallback(self, mock_get: MagicMock) -> None:
        """keyring raises exception → falls back to env."""
        mock_get.side_effect = Exception("Keyring error")
        manager = SecretManager()

        with patch.dict(os.environ, {"TEST_KEY": "env-value-456"}):
            result = manager.get("TEST_KEY")

        assert result == "env-value-456"

    @patch("keyring.set_password")
    def test_set_stores_in_keyring(self, mock_set: MagicMock) -> None:
        """keyring.set_password called correctly."""
        manager = SecretManager()

        manager.set("TEST_KEY", "secret-value-123")

        mock_set.assert_called_once_with(SERVICE_NAME, "TEST_KEY", "secret-value-123")

    @patch("keyring.get_password")
    def test_real_mode_env_forbidden(self, mock_get: MagicMock, tmp_path: Path) -> None:
        """TRADING_MODE=REAL + .env exists → RuntimeError."""
        mock_get.return_value = None
        manager = SecretManager()

        # Create .env file
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_KEY=value\n")

        with patch.dict(os.environ, {"TRADING_MODE": "REAL", "TEST_KEY": "env-value"}):
            with patch("pathlib.Path.cwd", return_value=tmp_path):
                with patch(
                    "pathlib.Path",
                    side_effect=lambda p: tmp_path / p if p == ".env" else Path(p),
                ):
                    # Manually set the path to check
                    with patch("config.secrets.Path") as mock_path:
                        mock_path.return_value.exists.return_value = True
                        with pytest.raises(
                            RuntimeError, match="REAL 모드에서 .env 파일 사용 금지"
                        ):
                            manager.get("TEST_KEY")

    @patch("keyring.get_password")
    def test_paper_mode_env_allowed(self, mock_get: MagicMock, tmp_path: Path) -> None:
        """TRADING_MODE=PAPER + .env exists → OK."""
        mock_get.return_value = None
        manager = SecretManager()

        # Create .env file with proper permissions
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_KEY=value\n")
        env_file.chmod(0o600)

        with patch.dict(os.environ, {"TRADING_MODE": "PAPER", "TEST_KEY": "env-value"}):
            with patch("config.secrets.Path") as mock_path:
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.stat.return_value.st_mode = stat.S_IFREG | 0o600
                result = manager.get("TEST_KEY")

        assert result == "env-value"


# ============================================================================
# _warn_env_permissions Tests
# ============================================================================


class TestWarnEnvPermissions:
    """_warn_env_permissions 함수 테스트."""

    @pytest.mark.skipif(os.name != "posix", reason="Unix only")
    def test_warn_env_wrong_permissions(self, tmp_path: Path) -> None:
        """.env with 0o644 → warning issued."""
        from config.secrets import _warn_env_permissions

        env_file = tmp_path / ".env"
        env_file.write_text("TEST=value\n")
        env_file.chmod(0o644)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_env_permissions(env_file)

            assert len(w) == 1
            assert "권한" in str(w[0].message)

    @pytest.mark.skipif(os.name != "posix", reason="Unix only")
    def test_no_warn_env_correct_permissions(self, tmp_path: Path) -> None:
        """.env with 0o600 → no warning."""
        from config.secrets import _warn_env_permissions

        env_file = tmp_path / ".env"
        env_file.write_text("TEST=value\n")
        env_file.chmod(0o600)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_env_permissions(env_file)

            assert len(w) == 0


# ============================================================================
# AuditLogger Integration Tests
# ============================================================================


class TestAuditLoggerIntegration:
    """AuditLogger와 redact_secrets 통합 테스트."""

    def test_logger_redacts_secrets(self, tmp_path: Path) -> None:
        """Create event with api_key field, log it, read JSONL → api_key value is redacted."""
        logger = AuditLogger(tmp_path)
        event = EventWithSecrets(
            api_key="sk-test-1234567890abcdefghij",
            app_secret="secret-value-123",
            access_token="token-xyz",
            symbol="AAPL",
            side="BUY",
            confidence=0.85,
        )

        logger.log(event)
        logger.close()

        # Read and parse the log file
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) == 1

        with open(log_files[0], "r") as f:
            line = f.readline()
            parsed = json.loads(line)

        # Verify secrets are redacted
        assert parsed["data"]["api_key"] == _REDACT_MARKER
        assert parsed["data"]["app_secret"] == _REDACT_MARKER
        assert parsed["data"]["access_token"] == _REDACT_MARKER

        # Verify non-secrets are preserved
        assert parsed["data"]["symbol"] == "AAPL"
        assert parsed["data"]["side"] == "BUY"
        assert parsed["data"]["confidence"] == 0.85

    def test_logger_redacts_nested_secrets(self, tmp_path: Path) -> None:
        """Nested secrets in event → redacted in log."""

        class NestedEvent(BaseModel):
            """Nested secrets event."""

            trace_id: str = Field(default_factory=lambda: str(uuid4()))
            user: dict[str, object] = Field(  # type: ignore[assignment]
                default_factory=lambda: {
                    "name": "Alice",
                    "credentials": {
                        "api_key": "sk-test-1234567890abcdefghij",
                        "password": "secret-pass-123",
                    },
                }
            )
            symbol: str = "AAPL"

        logger = AuditLogger(tmp_path)
        event = NestedEvent()

        logger.log(event)
        logger.close()

        # Read and parse the log file
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            parsed = json.loads(f.readline())

        # Verify nested secrets are redacted
        assert parsed["data"]["user"]["credentials"]["api_key"] == _REDACT_MARKER
        assert parsed["data"]["user"]["credentials"]["password"] == _REDACT_MARKER
        assert parsed["data"]["user"]["name"] == "Alice"
        assert parsed["data"]["symbol"] == "AAPL"
