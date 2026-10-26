"""Run the benchmark suite from the repository checkout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from automigrate.evaluation import run_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AutoMigrate benchmarks")
    parser.add_argument("--llm", default="mock", help="anthropic|openai|mock")
    parser.add_argument(
        "--cases", default="benchmarks/cases", help="Benchmark cases directory"
    )
    args = parser.parse_args(argv)
    results, metrics = run_evaluation(Path(args.cases), llm=args.llm)
    for item in results:
        marker = "PASS" if item.passed else "FAIL"
        print(f"[{marker}] {item.case.name}: {item.actual_status}")
    print(
        f"passed {metrics['cases_passed']}/{metrics['cases']} "
        f"in {metrics['execution_time_s']:.2f}s"
    )
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
