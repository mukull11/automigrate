from src.app import make_connection


def test_make_connection():
    assert make_connection() == "connected:db"
