from src.app import call_api


def test_call_api_works():
    assert call_api() in {"old", "new"}
