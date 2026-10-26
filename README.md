# AutoMigrate

AutoMigrate upgrades a Python repository from one major version of a dependency to
the next. It treats migration as a test-guided repair loop:

```
Detect -> Retrieve (RAG) -> Plan -> Patch -> Validate -> Diagnose -> (loop | Done | Flag)
```

Validation results drive the next action. If the system cannot safely repair a
failure it stops and flags the case for human review. It never forces a patch onto
the original repository.

## Requirements

- Windows PowerShell (the commands below were run there)
- Python 3.11
- No network access is required for the mock LLM and the benchmark suite

## Install

```powershell
python -m pip install -e ".[dev,ui]"
```

`dev` installs pytest, pytest-cov, and ruff. `ui` installs Streamlit.

## Quickstart

These are the exact commands used to verify the project.

```powershell
python -m ruff check src tests
python -m pytest -q
```

Run one benchmark case with the deterministic mock LLM:

```powershell
python -m automigrate migrate benchmarks\cases\01_rename\repo --dependency lib --from 1.x --to 2.x --docs benchmarks\cases\01_rename\docs --llm mock
```

Run the full benchmark suite and inspect the metrics:

```powershell
python -m automigrate eval --llm mock
Import-Csv benchmarks\results\metrics.csv | Format-Table case, expected_status, actual_status, files_modified, passed
```

Run the Streamlit demo UI:

```powershell
python -m streamlit run app/ui.py --server.headless true
```

Run tests with coverage (the project requires at least 80% on `src/automigrate`):

```powershell
python -m pytest -q --cov=automigrate --cov-report=term-missing
```

The helper script exposes the same tasks:

```powershell
.\scripts\tasks.ps1 lint
.\scripts\tasks.ps1 test
.\scripts\tasks.ps1 eval
.\scripts\tasks.ps1 ui
```

## CLI

```text
automigrate scan REPO
automigrate index-docs --docs DIR --dependency NAME
automigrate migrate REPO --dependency NAME --from 1.x --to 2.x --docs DIR
                 [--max-iterations 5] [--llm anthropic|openai|mock] [--out outputs/]
automigrate report SESSION_ID
automigrate eval [--llm mock] [--cases benchmarks/cases]
```

Exit codes: `0` = SUCCESSFUL, `2` = any flagged status, `1` = error.

## Safety

- The original repository is never modified. Every run copies it into a git-tracked
  workspace under `outputs/<session_id>/workspace`.
- Paths are resolved inside the workspace; traversal and escaping symlinks are
  rejected.
- The only external processes are `pytest`, `ruff`, and `git`, each with a fixed
  argument list and a timeout.
- Test files are read-only to the agent.
- API keys come from environment variables and are never written to logs, the
  database, or reports.
- When a run ends `REQUIRES_HUMAN_REVIEW` or `FAILED`, the workspace is reset to the
  baseline commit so no unverified patch is left behind.

## Documentation

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/evaluation.md`](docs/evaluation.md)

## License

MIT. See [`LICENSE`](LICENSE).
