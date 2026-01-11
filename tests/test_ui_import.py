import os


def test_import_ui_app():
    # Streamlit 통계 전송 비활성화(네트워크/유료 없음)
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    # 단순 import 스모크 테스트
    __import__("ui.app")

