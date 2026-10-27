from src.app import call_api


def test_call_api():
    assert call_api() == "new"
