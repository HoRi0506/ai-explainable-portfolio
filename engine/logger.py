"""
engine/logger.py - 감사 로깅 시스템 (Phase 1a).

Append-only JSONL + fsync + 일별 파일 로테이션 + SHA-256 체크섬.
Phase 1b에서 HMAC 키 서명 + 해시 체인 + 시퀀스 번호로 강화 예정.

제약사항:
- 단일 writer 전용. 동시에 여러 프로세스가 같은 로그에 쓰면 안 됨.
- 시크릿 리댁션은 A-5에서 별도 구현.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel


class AuditLogger:
    """감사 로그 기록기.

    일별 JSONL 파일에 이벤트를 기록. 각 쓰기 후 fsync로 crash safety 보장.
    파일명 형식: {log_dir}/audit_{YYYY-MM-DD}.jsonl
    """

    def __init__(self, log_dir: str | Path) -> None:
        """로그 디렉토리 설정. 디렉토리 없으면 생성.

        Args:
            log_dir: 로그 파일을 저장할 디렉토리 경로.
        """
        self._log_dir: Path = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str | None = None
        self._file: IO[Any] | None = None

    def _get_current_date(self) -> str:
        """현재 UTC 날짜 문자열 반환 (YYYY-MM-DD)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_file(self) -> IO[Any]:
        """현재 날짜의 로그 파일이 열려 있는지 확인. 날짜 바뀌면 로테이션.

        Returns:
            현재 날짜의 로그 파일 객체.
        """
        today = self._get_current_date()
        if self._file is None or self._current_date != today:
            if self._file is not None:
                self._file.flush()
                os.fsync(self._file.fileno())
                self._file.close()
            self._current_date = today
            path = self._log_dir / f"audit_{today}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
        return self._file

    def _get_log_path(self, date_str: str | None = None) -> Path:
        """특정 날짜의 로그 파일 경로 반환.

        Args:
            date_str: 날짜 문자열 (YYYY-MM-DD). None이면 현재 날짜 사용.

        Returns:
            로그 파일 경로.
        """
        date_str = date_str or self._get_current_date()
        return self._log_dir / f"audit_{date_str}.jsonl"

    def log(self, event: BaseModel, event_type: str | None = None) -> None:
        """이벤트를 JSONL로 기록.

        각 라인 형식:
        {"ts": "ISO8601-UTC", "event_type": "...", "trace_id": "...", "data": {...}}

        Args:
            event: Pydantic BaseModel 인스턴스.
            event_type: 이벤트 타입. 지정하지 않으면 클래스명 사용.

        Notes:
            - trace_id: event에 trace_id 필드가 있으면 추출
            - canonical JSON: sort_keys=True, separators=(',', ':') 고정
        """
        f = self._ensure_file()

        now = datetime.now(timezone.utc)
        data = event.model_dump(mode="json")

        # trace_id 추출 (있으면)
        trace_id = str(data.get("trace_id", ""))

        envelope: dict[str, Any] = {
            "ts": now.isoformat(),
            "event_type": event_type or event.__class__.__name__,
            "trace_id": trace_id,
            "data": data,
        }

        line = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        _ = f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())

    def close(self) -> None:
        """파일 플러시 + fsync + 닫기."""
        if self._file is not None:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._file = None
            self._current_date = None

    def __enter__(self) -> "AuditLogger":
        """Context manager entry."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit."""
        self.close()


def compute_checksum(log_path: Path) -> str:
    """JSONL 파일의 SHA-256 체크섬 계산.

    Args:
        log_path: 로그 파일 경로.

    Returns:
        SHA-256 체크섬 (16진수 문자열).
    """
    h = hashlib.sha256()
    with open(log_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            _ = h.update(chunk)
    return h.hexdigest()


def write_daily_checksum(log_path: Path, checksum_dir: Path | None = None) -> Path:
    """일별 체크섬 파일 작성 (atomic write).

    체크섬 파일: {checksum_dir}/{stem}.sha256
    Atomic write: temp file → fsync → os.replace

    Args:
        log_path: 로그 파일 경로.
        checksum_dir: 체크섬 파일을 저장할 디렉토리. None이면 log_path의 부모 디렉토리 사용.

    Returns:
        작성된 체크섬 파일 경로.
    """
    checksum_dir = checksum_dir or log_path.parent
    checksum_dir.mkdir(parents=True, exist_ok=True)

    checksum = compute_checksum(log_path)
    checksum_path = checksum_dir / f"{log_path.stem}.sha256"

    # Atomic write
    tmp_path = checksum_path.with_suffix(".sha256.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(f"{checksum}  {log_path.name}\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(checksum_path))

    return checksum_path


def verify_checksum(log_path: Path, checksum_path: Path) -> bool:
    """체크섬 파일과 실제 파일의 SHA-256 비교.

    Args:
        log_path: 로그 파일 경로.
        checksum_path: 체크섬 파일 경로.

    Returns:
        True면 무결성 확인, False면 변조 또는 불일치.
    """
    if not log_path.exists() or not checksum_path.exists():
        return False

    actual = compute_checksum(log_path)
    with open(checksum_path, "r", encoding="utf-8") as f:
        stored_line = f.readline().strip()
    stored_checksum = stored_line.split()[0] if stored_line else ""

    return actual == stored_checksum
