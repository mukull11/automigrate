import json

from automigrate.agent.triage import triage_candidates
from automigrate.ingest.versions import DependencyCandidate
from automigrate.llm.base import LLMAdapter


class ScriptedLLM(LLMAdapter):
    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, *, system=None, stage=None, iteration=0):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def _candidate(name: str, usages: int, *, status: str = "outdated") -> DependencyCandidate:
    return DependencyCandidate(
        name=name,
        current_version="1.0.0",
        latest_version="2.0.0",
        source_file="requirements.txt",
        usage_count=usages,
        status=status,
    )


def test_valid_triage_selection() -> None:
    candidates = [_candidate("numpy", 4), _candidate("requests", 1)]
    response = json.dumps(
        {
            "migrations": [
                {
                    "dependency": "numpy",
                    "from_version": "1.0.0",
                    "to_version": "2.0.0",
                    "reason": "most used",
                }
            ],
            "summary": "start with numpy",
        }
    )
    result = triage_candidates(ScriptedLLM([response]), candidates)
    assert [item.dependency for item in result.migrations] == ["numpy", "requests"]
    assert result.migrations[1].reason == "confirmed usages always migrate"
    assert result.summary == "start with numpy"


def test_unknown_dependency_is_rejected_then_retried() -> None:
    candidates = [_candidate("numpy", 4)]
    unknown = json.dumps(
        {
            "migrations": [
                {
                    "dependency": "ghost",
                    "from_version": "1.0.0",
                    "to_version": "2.0.0",
                    "reason": "bad",
                }
            ],
            "summary": "bad",
        }
    )
    valid = json.dumps(
        {
            "migrations": [
                {
                    "dependency": "numpy",
                    "from_version": "1.0.0",
                    "to_version": "2.0.0",
                    "reason": "ok",
                }
            ],
            "summary": "retry worked",
        }
    )
    llm = ScriptedLLM([unknown, valid])
    result = triage_candidates(llm, candidates)
    assert [item.dependency for item in result.migrations] == ["numpy"]
    assert len(llm.prompts) == 2
    assert "rejected" in llm.prompts[1]


def test_malformed_response_uses_deterministic_fallback() -> None:
    candidates = [_candidate("low", 1), _candidate("high", 9)]
    result = triage_candidates(ScriptedLLM(["not json", "still not json"]), candidates)
    assert [item.dependency for item in result.migrations] == ["high", "low"]
    assert "fallback" in result.summary.lower()


def test_max_dependencies_is_respected() -> None:
    candidates = [_candidate("a", 5), _candidate("b", 3)]
    response = json.dumps(
        {
            "migrations": [
                {
                    "dependency": "a",
                    "from_version": "1.0.0",
                    "to_version": "2.0.0",
                    "reason": "first",
                },
                {
                    "dependency": "b",
                    "from_version": "1.0.0",
                    "to_version": "2.0.0",
                    "reason": "second",
                },
            ],
            "summary": "two",
        }
    )
    result = triage_candidates(
        ScriptedLLM([response]), candidates, max_dependencies=1
    )
    assert [item.dependency for item in result.migrations] == ["a"]


def test_non_outdated_candidates_are_ignored() -> None:
    candidates = [_candidate("current", 5, status="current")]
    result = triage_candidates(ScriptedLLM([]), candidates)
    assert result.migrations == []
