"""engine/capability_token.py - HMAC capability token for Risk Gate → OMS pipeline (Phase 1b).

ApprovedOrderPlan의 정준 해시를 HMAC-SHA256으로 서명하여 위조/변조/만료/재사용을 방지.
토큰 형식: base64url(canonical_json).base64url(hmac_sha256_signature)

설계 결정:
- 숫자 필드는 문자열로 직렬화하여 float 비결정성 방지.
- exp/iat는 epoch seconds (int)로 표현.
- nonce(jti)는 UUID4 문자열. 검증 성공 시 자동 무효화 (1회 사용).
- hmac.compare_digest로 타이밍 공격 방지.
- token_manager=None이면 Phase 1a 동작 유지 (하위 호환).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import NamedTuple, cast
from uuid import uuid4

from schemas.models import ApprovedOrderPlan


class VerifyResult(NamedTuple):
    """Capability token 검증 결과.

    Attributes:
        valid: 검증 통과 여부.
        reason: 실패 사유 또는 성공 메시지.
    """

    valid: bool
    reason: str


class CapabilityTokenManager:
    """Risk Gate → OMS 전달용 HMAC capability token 관리자.

    Args:
        secret_key: HMAC 서명 시 사용할 비밀 키 바이트.
        default_ttl_seconds: generate() 기본 토큰 TTL(초).
        clock_skew_seconds: 허용 시계 오차(초).

    Raises:
        ValueError: secret_key가 비어 있거나 TTL/clock_skew가 음수이면 발생.
    """

    def __init__(
        self,
        secret_key: bytes,
        default_ttl_seconds: int = 60,
        clock_skew_seconds: int = 5,
        max_nonce_size: int = 10000,
    ) -> None:
        if not secret_key:
            raise ValueError("secret_key는 비어 있을 수 없습니다")
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds는 0보다 커야 합니다")
        if clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds는 0 이상이어야 합니다")
        if max_nonce_size <= 0:
            raise ValueError("max_nonce_size는 0보다 커야 합니다")

        self._secret_key: bytes = secret_key
        self._default_ttl_seconds: int = default_ttl_seconds
        self._clock_skew_seconds: int = clock_skew_seconds
        self._max_nonce_size: int = max_nonce_size
        self._used_nonces: dict[str, int] = {}

    def generate(self, plan: ApprovedOrderPlan, ttl_seconds: int | None = None) -> str:
        """ApprovedOrderPlan용 capability token을 생성한다.

        Args:
            plan: 서명 대상 주문 계획.
            ttl_seconds: 만료 시간(초). None이면 default_ttl_seconds 사용.

        Returns:
            ``base64url(payload).base64url(signature)`` 형식의 토큰 문자열.

        Raises:
            ValueError: ttl_seconds가 0 이하이면 발생.
        """

        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds는 0보다 커야 합니다")

        self._cleanup_expired_nonces()

        now = int(time.time())
        canonical = self._build_canonical_payload(
            plan=plan,
            exp=now + ttl,
            iat=now,
            jti=str(uuid4()),
        )
        payload_bytes = self._canonical_json_bytes(canonical)
        signature = hmac.new(self._secret_key, payload_bytes, hashlib.sha256).digest()
        return f"{self._b64url_encode(payload_bytes)}.{self._b64url_encode(signature)}"

    def verify(self, token: str, plan: ApprovedOrderPlan) -> VerifyResult:
        """Capability token을 검증한다.

        Args:
            token: ``generate()``로 생성된 토큰 문자열.
            plan: 검증 시점의 승인 주문 계획.

        Returns:
            VerifyResult(valid, reason).
        """

        self._cleanup_expired_nonces()

        parts = token.split(".")
        if len(parts) != 2:
            return VerifyResult(valid=False, reason="토큰 형식 오류")

        payload_part, signature_part = parts
        try:
            payload_bytes = self._b64url_decode(payload_part)
            signature = self._b64url_decode(signature_part)
        except (ValueError, TypeError, binascii.Error):
            return VerifyResult(valid=False, reason="토큰 base64 디코딩 실패")

        expected_signature = hmac.new(
            self._secret_key, payload_bytes, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            return VerifyResult(valid=False, reason="서명 검증 실패")

        try:
            payload_obj = json.loads(payload_bytes.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return VerifyResult(valid=False, reason="토큰 payload 파싱 실패")

        if not isinstance(payload_obj, dict):
            return VerifyResult(valid=False, reason="토큰 payload 타입 오류")
        payload_dict = cast(dict[str, object], payload_obj)

        required_fields = {
            "exp",
            "iat",
            "jti",
            "order_price",
            "order_qty",
            "order_side",
            "order_symbol",
            "order_type",
            "sizing_notional",
            "time_in_force",
            "trace_id",
        }
        if not required_fields.issubset(payload_dict):
            return VerifyResult(valid=False, reason="토큰 payload 필드 누락")

        exp_raw = payload_dict.get("exp")
        iat_raw = payload_dict.get("iat")
        jti_raw = payload_dict.get("jti")
        if not isinstance(exp_raw, int):
            return VerifyResult(valid=False, reason="토큰 exp 타입 오류")
        if not isinstance(iat_raw, int):
            return VerifyResult(valid=False, reason="토큰 iat 타입 오류")
        if not isinstance(jti_raw, str) or not jti_raw:
            return VerifyResult(valid=False, reason="토큰 jti 타입 오류")

        now = int(time.time())
        # 왜(why): 비정상 클레임을 조기 차단하여 pathological token 방지
        if exp_raw <= iat_raw:
            return VerifyResult(valid=False, reason="토큰 exp가 iat 이전")

        if iat_raw > now + self._clock_skew_seconds:
            return VerifyResult(valid=False, reason="토큰 iat가 미래")

        # max_ttl 제한: exp - iat가 default_ttl * 2를 초과하면 거부
        max_ttl = self._default_ttl_seconds * 2
        if (exp_raw - iat_raw) > max_ttl:
            return VerifyResult(valid=False, reason="토큰 TTL 초과")

        if exp_raw <= (now - self._clock_skew_seconds):
            return VerifyResult(valid=False, reason="토큰 만료")

        if jti_raw in self._used_nonces:
            return VerifyResult(valid=False, reason="토큰 재사용 감지")

        expected_payload_fields = self._build_canonical_payload(
            plan=plan,
            exp=exp_raw,
            iat=iat_raw,
            jti=jti_raw,
        )
        for key, expected_value in expected_payload_fields.items():
            # 왜(why): 서명 검증이 통과하더라도, 다른 plan에 재사용되는 confused deputy를 차단한다.
            if payload_dict.get(key) != expected_value:
                return VerifyResult(valid=False, reason=f"토큰 payload 불일치: {key}")

        # 왜(why): capability token은 1회성 권한 위임을 의도하므로, 성공 즉시 nonce를 소모한다.
        self._used_nonces[jti_raw] = exp_raw
        return VerifyResult(valid=True, reason="ok")

    def _cleanup_expired_nonces(self) -> None:
        """만료된 nonce를 정리하고, 최대 크기 초과 시 가장 오래된 항목을 제거한다."""

        now = int(time.time())
        expired_jtis = [
            jti
            for jti, exp in self._used_nonces.items()
            if exp + self._clock_skew_seconds < now
        ]
        for jti in expired_jtis:
            del self._used_nonces[jti]
        # 왜(why): 메모리 누수 방지 — 만료 정리 후에도 크기 초과 시 가장 오래된 nonce 제거
        if len(self._used_nonces) > self._max_nonce_size:
            sorted_by_exp = sorted(self._used_nonces.items(), key=lambda x: x[1])
            excess = len(self._used_nonces) - self._max_nonce_size
            for jti, _ in sorted_by_exp[:excess]:
                del self._used_nonces[jti]

    @staticmethod
    def _canonical_json_bytes(payload: dict[str, int | str]) -> bytes:
        """정준 JSON 바이트를 생성한다."""

        canonical_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return canonical_json.encode("ascii")

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        """padding 없는 base64url 인코딩 문자열을 반환한다."""

        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64url_decode(encoded: str) -> bytes:
        """padding 없는 base64url 문자열을 디코딩한다."""

        pad_len = (-len(encoded)) % 4
        return base64.urlsafe_b64decode(encoded + ("=" * pad_len))

    @staticmethod
    def _stringify_number_or_null(value: float | None) -> str:
        """숫자/None 값을 정준 문자열로 변환한다."""

        return "null" if value is None else str(value)

    def _build_canonical_payload(
        self,
        plan: ApprovedOrderPlan,
        exp: int,
        iat: int,
        jti: str,
    ) -> dict[str, int | str]:
        """ApprovedOrderPlan에서 정준 payload 사전을 구성한다."""

        return {
            "exp": exp,
            "iat": iat,
            "jti": jti,
            "order_price": self._stringify_number_or_null(plan.order.price),
            "order_qty": str(plan.order.qty),
            "order_side": plan.order.side.value,
            "order_symbol": plan.order.symbol,
            "order_type": plan.order.order_type,
            "sizing_notional": str(plan.sizing.notional),
            "time_in_force": plan.order.time_in_force,
            "trace_id": str(plan.trace_id),
        }
