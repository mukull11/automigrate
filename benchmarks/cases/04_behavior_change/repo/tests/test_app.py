from src.app import get_items


def test_get_items():
    assert get_items() == []
