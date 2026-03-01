"""
config/secrets.py - 시크릿 관리 및 로그 레닥션 (Phase 1a).

OS 키체인(keyring) 우선 조회 + .env 폴백.
로그 기록 시 민감 데이터를 자동으로 마스킹하는 redact_secrets() 제공.
"""

import copy
import os
import re
import stat
import warnings
from pathlib import Path
from typing import Any

# ============================================================================
# Constants
# ============================================================================

SERVICE_NAME = "trader-desktop"

_REDACT_MARKER = "***REDACTED***"
_REDACT_SUFFIXES = {"key", "secret", "token", "password"}
_REDACT_EXACT_KEYS = {"authorization", "passphrase", "signature"}

# 값 패턴 기반 마스킹 (정규식)
_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI key pattern
    re.compile(r"(?i)bearer\s+\S+"),  # Bearer tokens
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT tokens
]


# ============================================================================
# Helper Functions (Private)
# ============================================================================


def _warn_env_permissions(env_path: Path) -> None:
    """UNIX에서 .env 파일 권한이 0o600이 아니면 경고 출력.

    Windows에서는 skip (stat.S_IMODE 비적용).

    Args:
        env_path: .env 파일 경로.
    """
    if os.name != "posix":
        return
    mode = stat.S_IMODE(env_path.stat().st_mode)
    if mode != 0o600:
        warnings.warn(
            f".env 파일 권한이 {oct(mode)}입니다. 보안을 위해 'chmod 600 .env'를 실행하세요.",
            stacklevel=2,
        )


def _is_sensitive_key(key: str) -> bool:
    """키 이름이 민감한지 판단.

    접미사 기반: 키를 _/-로 분할 후 마지막 세그먼트가 _REDACT_SUFFIXES에 포함.
    정확 매칭: 키(소문자)가 _REDACT_EXACT_KEYS에 포함.

    Args:
        key: 키 이름.

    Returns:
        민감한 키면 True, 아니면 False.
    """
    lower = key.lower()
    if lower in _REDACT_EXACT_KEYS:
        return True
    # Split by _ or - and check last segment
    segments = re.split(r"[_-]", lower)
    return segments[-1] in _REDACT_SUFFIXES if segments else False


def _redact_value(value: str) -> str:
    """문자열 값 내 시크릿 패턴을 마스킹.

    Args:
        value: 원본 문자열.

    Returns:
        패턴이 마스킹된 문자열.
    """
    result = value
    for pattern in _VALUE_PATTERNS:
        result = pattern.sub(_REDACT_MARKER, result)
    return result


def _redact_recursive(obj: Any) -> None:
    """재귀적으로 dict/list를 순회하며 민감 데이터를 마스킹 (in-place).

    Args:
        obj: dict, list, 또는 기타 객체.
    """
    if isinstance(obj, dict):
        for k in obj:
            if _is_sensitive_key(k):
                if isinstance(obj[k], str):
                    obj[k] = _REDACT_MARKER
                elif obj[k] is not None:
                    obj[k] = _REDACT_MARKER
            elif isinstance(obj[k], str):
                obj[k] = _redact_value(obj[k])
            elif isinstance(obj[k], (dict, list)):
                _redact_recursive(obj[k])
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = _redact_value(item)
            elif isinstance(item, (dict, list)):
                _redact_recursive(item)


# ============================================================================
# Public Functions
# ============================================================================


def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """로깅용 민감 데이터 마스킹.

    키 이름 접미사 기반 + 값 패턴 기반으로 시크릿을 "***REDACTED***"로 치환.
    원본 데이터를 변경하지 않음 (deep copy 반환).

    Args:
        data: Pydantic model_dump() 결과 등의 딕셔너리.

    Returns:
        민감 데이터가 마스킹된 딕셔너리 (새 객체).
    """
    result = copy.deepcopy(data)
    _redact_recursive(result)
    return result


# ============================================================================
# SecretManager Class
# ============================================================================


class SecretManager:
    """OS 키체인 우선, .env 폴백 시크릿 로더.

    조회 순서: keyring → .env → None.
    REAL 모드에서는 .env 사용을 금지하여 보안 강화.
    """

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        """초기화.

        Args:
            service_name: OS 키체인 서비스 이름. 기본값: "trader-desktop".
        """
        self._service: str = service_name

    def get(self, key: str) -> str | None:
        """시크릿 조회. keyring → .env fallback → None.

        Args:
            key: 환경변수/시크릿 키 이름 (예: "KIS_APP_KEY").

        Returns:
            시크릿 값 또는 None.

        Raises:
            RuntimeError: REAL 모드에서 .env 폴백 시도 시.
        """
        # 1) keyring 시도 (NoKeyringError 등 graceful handling)
        try:
            import keyring

            value = keyring.get_password(self._service, key)
            if value is not None:
                return value
        except Exception:
            # keyring 백엔드 없음, 잠금 등 → 폴백으로 진행
            pass

        # 2) .env 폴백 — REAL 모드 금지
        env_value = os.environ.get(key)
        if env_value is not None:
            # .env에서 온 값인지 체크: .env 파일 존재 여부로 판단
            env_path = Path(".env")
            if env_path.exists():
                trading_mode = os.environ.get("TRADING_MODE", "PAUSED").upper()
                if trading_mode == "REAL":
                    raise RuntimeError(
                        f"REAL \ubaa8\ub4dc\uc5d0\uc11c .env \ud30c\uc77c \uc0ac\uc6a9 \uae08\uc9c0. '{key}'\ub97c OS \ud0a4\uccb4\uc778\uc5d0 \uc800\uc7a5\ud558\uc138\uc694: python -c \"import keyring; keyring.set_password('{SERVICE_NAME}', '{key}', 'YOUR_VALUE')\""
                    )
                _warn_env_permissions(env_path)
            return env_value

        return None

    def set(self, key: str, value: str) -> None:
        """시크릿을 OS 키체인에 저장.

        Args:
            key: 시크릿 키 이름.
            value: 시크릿 값.

        Raises:
            RuntimeError: keyring 백엔드 사용 불가 시.
        """
        import keyring

        keyring.set_password(self._service, key, value)
