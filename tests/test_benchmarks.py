import json
from pathlib import Path

from automigrate.evaluation import discover_cases, run_evaluation
from automigrate.rag.chunker import chunk_text

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "benchmarks" / "cases"
RESULTS = ROOT / "benchmarks" / "results"


def test_case_layout_and_citations() -> None:
    cases = discover_cases(CASES)
    assert [case.name for case in cases] == [
        "01_rename",
        "02_param_removed",
        "03_import_path",
        "04_behavior_change",
        "05_unrelated_failure",
    ]
    for case in cases:
        assert case.repo.is_dir()
        assert case.docs.is_dir()
        assert (case.path / "plan.json").is_file()
        assert (case.path / "patch.txt").is_file()
        assert (case.path / "case.yaml").is_file()
        assert (case.path / "fake_lib" / "old_version").is_dir()
        assert (case.path / "fake_lib" / "new_version").is_dir()

        allowed = set()
        for doc in case.docs.iterdir():
            if doc.is_file():
                allowed.update(
                    chunk.chunk_id
                    for chunk in chunk_text(
                        doc.read_text(encoding="utf-8"), doc.name
                    )
                )
        plan = json.loads((case.path / "plan.json").read_text(encoding="utf-8"))
        cited = {
            chunk_id
            for change in plan["changes"]
            for chunk_id in change["cites"]
        }
        assert cited
        assert cited <= allowed


def test_benchmark_cases_match_expected_outcomes() -> None:
    results, metrics = run_evaluation(CASES, llm="mock", results_dir=RESULTS)
    assert metrics["cases_passed"] == 5
    assert all(item.passed for item in results)
    assert (RESULTS / "metrics.csv").is_file()
    assert (RESULTS / "results.md").is_file()

    by_name = {item.case.name: item for item in results}
    assert by_name["01_rename"].actual_status == "SUCCESSFUL"
    assert by_name["02_param_removed"].actual_status == "SUCCESSFUL"
    assert by_name["03_import_path"].actual_status == "SUCCESSFUL"
    assert by_name["04_behavior_change"].actual_status == "REQUIRES_HUMAN_REVIEW"
    assert by_name["04_behavior_change"].actual_diagnosis == "INSUFFICIENT_EVIDENCE"
    assert by_name["05_unrelated_failure"].actual_status in {
        "FAILED",
        "REQUIRES_HUMAN_REVIEW",
    }
    assert by_name["05_unrelated_failure"].actual_diagnosis == "UNRELATED"
    for name in ("04_behavior_change", "05_unrelated_failure"):
        assert by_name[name].tests_unchanged
        assert by_name[name].final_failed > 0
