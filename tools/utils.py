"""공통 유틸 (로깅/캐시/백오프/HTTP) — 간단 버전"""

from __future__ import annotations

def get_logger(name: str):
    import logging
    import os
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # 파일 로그 핸들러 추가
        try:
            os.makedirs(".logs", exist_ok=True)
            fhandler = logging.FileHandler(os.path.join(".logs", "app.log"), encoding="utf-8")
            fhandler.setFormatter(fmt)
            logger.addHandler(fhandler)
        except Exception:
            # 파일 로그 실패는 무시
            pass
    return logger

def disk_cache_path(namespace: str, key: str) -> str:
    import hashlib, os
    root = os.path.join(".cache", namespace)
    os.makedirs(root, exist_ok=True)
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return os.path.join(root, f"{h}.json")

def load_cache(path: str):
    import json, os
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_cache(path: str, data) -> None:
    import json, os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def request_json(url: str, params: dict | None = None, headers: dict | None = None,
                 timeout: float = 30.0, attempts: int = 4) -> dict | None:
    """HTTP GET→JSON with retries/backoff; 실패 시 None 반환.
    외부 API 사용: 무료 공개 엔드포인트만 테스트에서 사용.
    """
    import httpx
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

    log = get_logger(__name__)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(attempts),
        reraise=False,
    )
    def _do() -> dict | None:
        with httpx.Client(timeout=timeout, headers=headers or {}) as client:
            resp = client.get(url, params=params)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return None

    try:
        return _do()
    except Exception as e:
        log.warning(f"request_json 실패: {e}")
        return None
