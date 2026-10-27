import json
from pathlib import Path

from automigrate import db
from automigrate.llm.openai import DEFAULT_MODEL, OpenAILLM
from automigrate.llm.recording import RecordingLLM
from automigrate.report import write_report

TEST_KEY = "test-secret-key-not-real"


def _http_module():
    try:
        import httpx2 as http
    except ImportError:  # pragma: no cover - older openai uses httpx
        import httpx as http
    return http


def _mock_client(api_key: str, captured: dict | None = None):
    http = _http_module()

    def handler(request):
        if captured is not None:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
        return http.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": DEFAULT_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    return http.Client(transport=http.MockTransport(handler))


def test_openai_reads_env_base_url_key_and_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AUTOMIGRATE_MODEL", "gemini-2.5-flash")
    monkeypatch.delenv("AUTOMIGRATE_OPENAI_MODEL", raising=False)

    llm = OpenAILLM()

    assert llm.model == "gemini-2.5-flash"
    assert llm._client.api_key == TEST_KEY
    assert str(llm._client.base_url) == "https://example.test/v1/"


def test_openai_explicit_api_key_is_passed_to_client(monkeypatch) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    OpenAILLM(model="test-model", api_key="test-key-123")

    assert captured["api_key"] == "test-key-123"


def test_openai_request_is_built_with_model_messages_and_key() -> None:
    captured: dict = {}
    # The real client needs a base_url too; build it through the SDK.
    from openai import OpenAI

    client = OpenAI(
        api_key=TEST_KEY,
        base_url="https://example.test/v1",
        http_client=_mock_client(TEST_KEY, captured),
    )
    llm = OpenAILLM(model="gemini-2.5-flash", client=client)

    text = llm.complete("hello", system="be terse", stage="PLAN", iteration=1)

    assert text == '{"ok": true}'
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["authorization"] == f"Bearer {TEST_KEY}"
    body = captured["body"]
    assert body["model"] == "gemini-2.5-flash"
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]


def test_openai_key_not_logged_or_written_to_report(tmp_path: Path) -> None:
    from openai import OpenAI

    client = OpenAI(
        api_key=TEST_KEY,
        base_url="https://example.test/v1",
        http_client=_mock_client(TEST_KEY),
    )
    llm = OpenAILLM(client=client)

    conn = db.init_db(tmp_path / "automigrate.db")
    repo_id = db.create_repository(conn, "demo", tmp_path / "repo")
    dependency_id = db.create_dependency(conn, repo_id, "lib", "1.x", "2.x")
    session_id = db.create_session(conn, repo_id, dependency_id)
    try:
        RecordingLLM(llm, conn, session_id).complete("hello", stage="PLAN")
        rows = conn.execute("SELECT * FROM agent_action").fetchall()
        assert TEST_KEY not in json.dumps([dict(row) for row in rows])

        result = write_report(conn, session_id, tmp_path / "outputs")
        markdown = (tmp_path / "outputs" / session_id / "report.md").read_text(
            encoding="utf-8"
        )
        raw_json = (tmp_path / "outputs" / session_id / "report.json").read_text(
            encoding="utf-8"
        )
        assert TEST_KEY not in result.markdown
        assert TEST_KEY not in markdown
        assert TEST_KEY not in raw_json
    finally:
        conn.close()
