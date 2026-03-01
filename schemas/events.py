"""
schemas/events.py - 이벤트 스키마.

주요 이벤트:
- Alert: Monitor Agent의 알림 (포지션 감시, 이상 감지)
- ConfigChangeEvent: 설정 변경 이벤트 (감사 로그용)

모든 datetime은 UTC timezone-aware.
"""

from datetime import datetime

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from schemas.models import AlertAction, AlertSeverity


class Alert(BaseModel):
    """Monitor Agent의 알림. 포지션 감시, 이상 감지."""

    trace_id: UUID = Field(default_factory=uuid4)
    """추적 ID (UUID v4)."""
    ts: datetime
    """알림 시각 (UTC timezone-aware)."""
    severity: AlertSeverity
    """심각도 (LOW/MEDIUM/HIGH/CRITICAL)."""
    message: str
    """알림 메시지."""
    action: AlertAction
    """권장 조치 (HOLD/REDUCE/STOP)."""


class ConfigChangeEvent(BaseModel):
    """설정 변경 이벤트. 감사 로그용."""

    timestamp: datetime
    """변경 시각 (UTC timezone-aware)."""
    changed_by: str
    """변경자 (사용자명 또는 시스템)."""
    old_value: object = None
    """이전 값."""
    new_value: object = None
    """새 값."""
    version_tag: str = ""
    """버전 태그 (선택)."""
    approval_log: str | None = None
    """승인 로그 (선택)."""
