import os
from tools.utils import disk_cache_path, save_cache, load_cache, get_logger


def test_cache_roundtrip(tmp_path):
    p = disk_cache_path("unittest", "hello")
    # 강제로 임시 디렉토리를 사용하도록 경로 앞에 tmp를 붙이는 대신, 실제 .cache 경로에 씀
    data = {"x": 1}
    save_cache(p, data)
    got = load_cache(p)
    assert got == data


def test_logger_writes_file():
    log = get_logger("unittest")
    log.info("test-message")
    assert os.path.exists(".logs/app.log")
    # 파일 크기가 0보다 큰지 확인
    assert os.path.getsize(".logs/app.log") > 0
