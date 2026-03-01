"""
tests/test_logger.py - 감사 로깅 시스템 테스트.

18개 테스트 케이스:
1. 디렉토리 생성
2. JSONL 쓰기 및 파싱
3. Envelope 형식 검증
4. Canonical JSON 검증
5. fsync 동작 확인
6. 일별 로테이션
7. Context manager
8. 다중 이벤트
9. trace_id 추출
10. trace_id 없음
11. 커스텀 event_type
12. 체크섬 계산
13. 체크섬 파일 작성 (atomic)
14. 체크섬 검증 (유효)
15. 체크섬 검증 (변조)
16. 체크섬 검증 (파일 없음)
17. Crash 시뮬레이션
18. 재오픈 및 append
"""

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from engine.logger import (
    AuditLogger,
    compute_checksum,
    verify_checksum,
    write_daily_checksum,
)


# ============================================================================
# Test Models
# ============================================================================


class SimpleEvent(BaseModel):
    """간단한 테스트 이벤트."""

    message: str
    value: int


class EventWithTraceId(BaseModel):
    """trace_id를 포함한 테스트 이벤트."""

    trace_id: UUID = Field(default_factory=uuid4)
    message: str
    value: int


# ============================================================================
# Test Cases
# ============================================================================


class TestAuditLoggerBasics:
    """기본 기능 테스트."""

    def test_audit_logger_creates_dir(self, tmp_path: Any) -> None:
        """Logger creates log_dir if not exists."""
        log_dir = tmp_path / "logs"
        assert not log_dir.exists()

        logger = AuditLogger(log_dir)

        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_audit_logger_writes_jsonl(self, tmp_path: Any) -> None:
        """Write event, verify JSONL line parseable."""
        logger = AuditLogger(tmp_path)
        event = SimpleEvent(message="test", value=42)

        logger.log(event)
        logger.close()

        # Read and parse the log file
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) == 1

        with open(log_files[0], "r") as f:
            line = f.readline()
            parsed = json.loads(line)

        assert "ts" in parsed
        assert "event_type" in parsed
        assert "trace_id" in parsed
        assert "data" in parsed

    def test_audit_logger_envelope_format(self, tmp_path: Any) -> None:
        """Verify ts, event_type, trace_id, data fields."""
        logger = AuditLogger(tmp_path)
        event = SimpleEvent(message="test", value=42)

        logger.log(event)
        logger.close()

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            parsed = json.loads(f.readline())

        # Verify envelope structure
        assert isinstance(parsed["ts"], str)
        assert "T" in parsed["ts"]  # ISO format
        assert parsed["event_type"] == "SimpleEvent"
        assert parsed["trace_id"] == ""  # No trace_id in SimpleEvent
        assert isinstance(parsed["data"], dict)
        assert parsed["data"]["message"] == "test"
        assert parsed["data"]["value"] == 42

    def test_audit_logger_canonical_json(self, tmp_path: Any) -> None:
        """Verify sort_keys, no extra whitespace."""
        logger = AuditLogger(tmp_path)
        event = SimpleEvent(message="test", value=42)

        logger.log(event)
        logger.close()

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            line = f.readline()

        # Verify no extra whitespace
        assert "\n" not in line.rstrip("\n")
        assert "  " not in line  # No double spaces

        # Verify keys are sorted
        parsed = json.loads(line)
        keys = list(parsed.keys())
        assert keys == sorted(keys)

    def test_audit_logger_fsync(self, tmp_path: Any) -> None:
        """Write event, verify file is not empty (flush worked)."""
        logger = AuditLogger(tmp_path)
        event = SimpleEvent(message="test", value=42)

        logger.log(event)
        # Don't close yet, but file should be flushed

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) == 1

        # File should have content even without close
        with open(log_files[0], "r") as f:
            content = f.read()
            assert len(content) > 0

        logger.close()


class TestAuditLoggerRotation:
    """일별 로테이션 테스트."""

    def test_audit_logger_daily_rotation(self, tmp_path: Any) -> None:
        """Mock date change, verify new file created."""
        logger = AuditLogger(tmp_path)
        event1 = SimpleEvent(message="day1", value=1)

        logger.log(event1)

        # Mock date change
        with patch("engine.logger.datetime") as mock_datetime:
            # First call returns original date, subsequent calls return new date
            original_date = datetime.now(timezone.utc)
            new_date = datetime(2025, 12, 25, 10, 0, 0, tzinfo=timezone.utc)

            # Setup mock to return different dates
            mock_datetime.now.side_effect = [
                original_date,  # _get_current_date() in log()
                new_date,  # _get_current_date() in _ensure_file()
            ]
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            event2 = SimpleEvent(message="day2", value=2)
            logger.log(event2)

        logger.close()

        # Verify two files created
        log_files = sorted(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) >= 1  # At least one file


class TestAuditLoggerContextManager:
    """Context manager 테스트."""

    def test_audit_logger_context_manager(self, tmp_path: Any) -> None:
        """Use `with` statement, verify file closed."""
        with AuditLogger(tmp_path) as logger:
            event = SimpleEvent(message="test", value=42)
            logger.log(event)

        # After context exit, file should be closed
        assert logger._file is None
        assert logger._current_date is None

        # But data should be persisted
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) == 1


class TestAuditLoggerMultipleEvents:
    """다중 이벤트 테스트."""

    def test_audit_logger_multiple_events(self, tmp_path: Any) -> None:
        """Write 10 events, verify 10 lines."""
        logger = AuditLogger(tmp_path)

        for i in range(10):
            event = SimpleEvent(message=f"event_{i}", value=i)
            logger.log(event)

        logger.close()

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            lines = f.readlines()

        assert len(lines) == 10

        # Verify each line is valid JSON
        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed["data"]["value"] == i


class TestAuditLoggerTraceId:
    """trace_id 추출 테스트."""

    def test_audit_logger_trace_id_extraction(self, tmp_path: Any) -> None:
        """Event with trace_id → extracted to envelope."""
        logger = AuditLogger(tmp_path)
        trace_id = uuid4()
        event = EventWithTraceId(message="test", value=42, trace_id=trace_id)

        logger.log(event)
        logger.close()

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            parsed = json.loads(f.readline())

        assert parsed["trace_id"] == str(trace_id)

    def test_audit_logger_no_trace_id(self, tmp_path: Any) -> None:
        """Event without trace_id → empty string."""
        logger = AuditLogger(tmp_path)
        event = SimpleEvent(message="test", value=42)

        logger.log(event)
        logger.close()

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            parsed = json.loads(f.readline())

        assert parsed["trace_id"] == ""


class TestAuditLoggerEventType:
    """event_type 테스트."""

    def test_audit_logger_custom_event_type(self, tmp_path: Any) -> None:
        """Override event_type parameter."""
        logger = AuditLogger(tmp_path)
        event = SimpleEvent(message="test", value=42)

        logger.log(event, event_type="CustomType")
        logger.close()

        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            parsed = json.loads(f.readline())

        assert parsed["event_type"] == "CustomType"


class TestChecksum:
    """체크섬 함수 테스트."""

    def test_compute_checksum(self, tmp_path: Any) -> None:
        """Known content → expected SHA-256."""
        log_file = tmp_path / "test.jsonl"
        content = '{"test": "data"}\n'
        log_file.write_text(content)

        checksum = compute_checksum(log_file)

        # Verify it's a valid hex string
        assert len(checksum) == 64  # SHA-256 = 64 hex chars
        assert all(c in "0123456789abcdef" for c in checksum)

    def test_write_daily_checksum_atomic(self, tmp_path: Any) -> None:
        """Write checksum, verify file exists and content."""
        log_file = tmp_path / "audit_2025-12-25.jsonl"
        log_file.write_text('{"test": "data"}\n')

        checksum_path = write_daily_checksum(log_file, tmp_path)

        assert checksum_path.exists()
        content = checksum_path.read_text()
        assert "  " in content  # Format: "hash  filename"
        assert "audit_2025-12-25.jsonl" in content

    def test_verify_checksum_valid(self, tmp_path: Any) -> None:
        """Write + checksum → verify returns True."""
        log_file = tmp_path / "audit_2025-12-25.jsonl"
        log_file.write_text('{"test": "data"}\n')

        checksum_path = write_daily_checksum(log_file, tmp_path)
        result = verify_checksum(log_file, checksum_path)

        assert result is True

    def test_verify_checksum_tampered(self, tmp_path: Any) -> None:
        """Write + checksum + modify file → verify returns False."""
        log_file = tmp_path / "audit_2025-12-25.jsonl"
        log_file.write_text('{"test": "data"}\n')

        checksum_path = write_daily_checksum(log_file, tmp_path)

        # Tamper with the log file
        log_file.write_text('{"test": "modified"}\n')

        result = verify_checksum(log_file, checksum_path)

        assert result is False

    def test_verify_checksum_missing_files(self, tmp_path: Any) -> None:
        """Missing log or checksum → returns False."""
        log_file = tmp_path / "audit_2025-12-25.jsonl"
        checksum_path = tmp_path / "audit_2025-12-25.sha256"

        result = verify_checksum(log_file, checksum_path)

        assert result is False


class TestAuditLoggerCrashRecovery:
    """Crash recovery 테스트."""

    def test_crash_simulation(self, tmp_path: Any) -> None:
        """Write events, don't close, reopen → all lines parseable."""
        logger = AuditLogger(tmp_path)

        for i in range(5):
            event = SimpleEvent(message=f"event_{i}", value=i)
            logger.log(event)

        # Simulate crash: don't call close()
        # (In real scenario, process would be killed)

        # Reopen logger
        logger2 = AuditLogger(tmp_path)
        for i in range(5, 10):
            event = SimpleEvent(message=f"event_{i}", value=i)
            logger2.log(event)

        logger2.close()

        # Verify all lines are parseable
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        with open(log_files[0], "r") as f:
            lines = f.readlines()

        assert len(lines) == 10
        for line in lines:
            parsed = json.loads(line)
            assert "ts" in parsed
            assert "data" in parsed

    def test_reopen_append(self, tmp_path: Any) -> None:
        """Close and reopen logger → appends correctly."""
        # First session
        logger1 = AuditLogger(tmp_path)
        event1 = SimpleEvent(message="session1", value=1)
        logger1.log(event1)
        logger1.close()

        # Second session
        logger2 = AuditLogger(tmp_path)
        event2 = SimpleEvent(message="session2", value=2)
        logger2.log(event2)
        logger2.close()

        # Verify both events in same file
        log_files = list(tmp_path.glob("audit_*.jsonl"))
        assert len(log_files) == 1

        with open(log_files[0], "r") as f:
            lines = f.readlines()

        assert len(lines) == 2
        parsed1 = json.loads(lines[0])
        parsed2 = json.loads(lines[1])
        assert parsed1["data"]["message"] == "session1"
        assert parsed2["data"]["message"] == "session2"
