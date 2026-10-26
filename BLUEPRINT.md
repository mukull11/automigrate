# AutoMigrate Blueprint

Authoritative spec for building AutoMigrate. If this file conflicts with another
note, this file wins. Build exactly what is written here.

## 1. Purpose

AutoMigrate upgrades a Python repository's dependency from one major version to
another. It treats migration as a test-guided repair loop:

  Detect -> Retrieve (RAG) -> Plan -> Patch -> Validate -> Diagnose -> (loop | Done | Flag)

Core principle: validation results drive the next action. When the system
cannot safely repair a failure, it stops and flags the case for human review.
It never forces a patch.

## 2. Non-goals

- No hosted web service. The product is a local CLI, with a Streamlit UI as a demo front end.
- No arbitrary shell execution by the agent.
- No multi-language support (Python only).
- No automatic pull requests or CI integration.
- No guarantee of correctness beyond the repository's own tests.

## 3. Glossary

- Repo: the user's original Python project. Never modified.
- Workspace: a git-tracked copy of the repo where all edits happen.
- Usage: one place in source code where the dependency is used (import, call, attribute, keyword argument).
- Evidence: a retrieved documentation chunk with a stable chunk_id.
- Plan: a structured, validated list of intended changes, each citing evidence.
- Patch: SEARCH/REPLACE blocks applied to workspace files.
- Iteration: one pass through PLAN -> PATCH -> VALIDATE -> DIAGNOSE. Iteration 0 is the baseline validation before any edit.
- Flag: stop the run and mark it REQUIRES_HUMAN_REVIEW (or PARTIAL/FAILED as defined in section 6).

## 4. Architecture

Layers, from top to bottom:

1. Interfaces: `cli.py` (Typer) and `app/ui.py` (Streamlit). Both call only `graph.run_migration()`. Neither contains pipeline logic.
2. Orchestrator: `graph.py`, an explicit state machine (section 5).
3. Agents: `agent/planner.py`, `agent/patcher.py`, `agent/diagnoser.py`. Each builds prompts, calls the LLM, and validates output.
4. RAG: `rag/` (loaders, chunker, index, retriever).
5. Tools: `tools/` (sandboxed filesystem, pytest/ruff runners, git operations). The only external processes are pytest, ruff, and git.
6. Persistence: `db.py` (SQLite). Every stage transition and LLM call is recorded.
7. LLM adapters: `llm/` (anthropic, openai, mock). All implement one interface.

Dependency rule: higher layers may import lower layers. Tools and RAG never import agent or graph code.

## 5. State machine

States: INGEST, DETECT, RETRIEVE, PLAN, PATCH, VALIDATE, DIAGNOSE, REPORT, and terminal states DONE and FLAGGED.

| From      | Condition                                                     | To        |
|-----------|---------------------------------------------------------------|-----------|
| (start)   | always; run baseline validation as iteration 0                | INGEST    |
| INGEST    | at least one .py file and the dependency is declared          | DETECT    |
| INGEST    | dependency not declared in any supported file                 | FLAGGED   |
| DETECT    | at least one usage found                                      | RETRIEVE  |
| DETECT    | zero usages                                                   | FLAGGED   |
| RETRIEVE  | at least one evidence chunk above the score threshold         | PLAN      |
| RETRIEVE  | no evidence at all                                            | FLAGGED   |
| PLAN      | valid plan with at least one change of confidence >= 0.5      | PATCH     |
| PLAN      | invalid JSON or schema after one retry                        | FLAGGED   |
| PLAN      | every change has confidence < 0.5                             | FLAGGED   |
| PATCH     | all blocks apply (each SEARCH matches exactly once)           | VALIDATE  |
| PATCH     | any block fails after one LLM retry                           | FLAGGED   |
| VALIDATE  | always                                                        | DIAGNOSE  |
| DIAGNOSE  | all tests pass and no new ruff issues                         | REPORT -> DONE |
| DIAGNOSE  | failing, diagnosis MIGRATION_RELATED or POSSIBLY_RELATED, iteration < max, pass count did not drop | PLAN (iteration + 1) |
| DIAGNOSE  | failing, diagnosis UNRELATED or INSUFFICIENT_EVIDENCE         | REPORT -> FLAGGED |
| DIAGNOSE  | failing and iteration >= max_iterations                       | REPORT -> FLAGGED |
| DIAGNOSE  | pass count dropped compared with the previous iteration       | roll back workspace to previous commit, then REPORT -> FLAGGED |

Rules:
- Each transition writes one `agent_action` row (stage, iteration, summary, latency).
- The iteration counter increments only when entering PLAN from DIAGNOSE.
- Default max_iterations is 5.
- Final status mapping:
  - DONE -> SUCCESSFUL
  - FLAGGED with pass count improved over baseline -> PARTIAL
  - FLAGGED with pass count unchanged or worse, or no edit made -> REQUIRES_HUMAN_REVIEW if the cause is ambiguous, FAILED if the tool itself errored
- The flag reason is always stored and always shown in the report.

## 6. Data contracts (pydantic models in `state.py`)

```
Usage       file: str, line: int, symbol: str, kind: Literal[import|call|attribute|keyword_arg], snippet: str
Evidence    chunk_id: str, source_doc: str, heading_path: str, score: float, text: str (<= 800 chars)
PlannedChange  file: str, old_symbol: str, new_symbol: str | None, description: str,
               confidence: float (0..1), cites: list[str]   # chunk_ids
Plan        summary: str, changes: list[PlannedChange], expected_effect: str
TestRunResult  passed: int, failed: int, errors: int, failed_test_names: list[str],
               first_traceback: str | None, ruff_issue_count: int, duration_s: float
AgentState  session_id, repo_path, workspace_path, dependency, from_version, to_version,
            usages, evidence, plan, iteration, max_iterations=5, baseline: TestRunResult,
            latest: TestRunResult, diagnosis, status, flag_reason, history
```

Rule: a plan may cite only chunk_ids that retrieval returned in this iteration. Citations to unknown chunk_ids make the plan invalid.

## 7. Patch format (exact)

The LLM must output only this format:

```
FILE: relative/path/to/file.py
<<<<<<< SEARCH
exact existing lines, copied verbatim
=======
replacement lines
>>>>>>> REPLACE
```

Application rules:
- The SEARCH text must occur exactly once in the target file. Zero or multiple matches are rejected.
- Blocks are applied in order. If any block fails, no partial write persists. Restore the file state from before PATCH.
- After a successful PATCH, commit in the workspace with message `automigrate: iter {n} - {summary}`.
- Store `git diff` of the commit in `migration_change`.

## 8. RAG specification

Ingestion:
- Input: a docs directory containing .md, .rst, .txt, or .html files.
- Chunking: split on headings, then on paragraphs to roughly 600 tokens (words x 1.3), with 80-token overlap.
- Each chunk stores: source_doc, heading_path (e.g. "Migration Guide > Removed parameters"), text, dependency, version_hint.
- chunk_id = sha1(source_doc + text).

Index:
- Store chunks in SQLite table `doc_chunk`.
- Keyword search: BM25 over tokens. Tokenizer keeps identifiers intact (`old_api`, `mode`, `lib.core.helpers`), lowercased.
- Optional semantic search: only if `sentence-transformers` is installed. Fuse keyword and semantic rankings with reciprocal rank fusion (k=60). Without it, use BM25 only. The system must work with BM25 alone.
- Rebuild the index only when a file's sha256 changes.

Query construction:
- Terms: dependency name, from_version, to_version, every symbol from DETECT.
- On iteration >= 2: add the first 300 characters of the latest traceback.
- Return top-k = 6 evidence items. Score threshold is configurable (default 0.0 with a warning).

## 9. Safety rules (hard requirements)

1. The original repo is never written to. All edits happen in the workspace, which is a copy created at run start.
2. Every path must resolve inside the workspace root. Reject `..` components and symlinks that point outside. Tests must verify this.
3. Subprocess allowlist: only `pytest`, `ruff`, and `git`, each called with a fixed argument list, `shell=False`, and a timeout (120s by default).
4. Test files are read-only to the agent by default. The agent may not edit files under `tests/` or `test_*.py` unless the blueprint says so for a specific case. Benchmark cases use this rule to check that the agent doesn't "fix" tests to pass.
5. Secrets: API keys come from environment variables only. Never write them to logs, the database, or reports.
6. LLM output is untrusted. It is parsed by strict parsers and validated by pydantic. Invalid output never executes.

## 10. Persistence (SQLite, default `./automigrate.db`)

Tables:
- repository(id, name, path, language, created_at)
- dependency(id, repo_id, name, source_version, target_version)
- migration_session(id, repo_id, dependency_id, started_at, ended_at, status, iterations, final_reason)
- source_file(id, session_id, path, sha256, modified)
- agent_action(id, session_id, iteration, stage, action_type, input_summary, output_summary, latency_ms, created_at)
- test_run(id, session_id, iteration, passed, failed, errors, ruff_issues, duration_s, failed_tests_json, traceback)
- migration_change(id, session_id, iteration, file, description, diff, commit_sha)
- doc_chunk(id, source_doc, heading_path, text, dependency, version_hint, sha256)
- migration_report(session_id PK, status, metrics_json, report_path)
- schema_version(version)

All SQL lives in `db.py`. Other modules call functions, not SQL.

## 11. CLI

```
automigrate scan REPO
automigrate index-docs --docs DIR --dependency NAME
automigrate migrate REPO --dependency NAME --from 1.x --to 2.x --docs DIR
                 [--max-iterations 5] [--llm anthropic|openai|mock] [--out outputs/]
automigrate report SESSION_ID
automigrate eval [--llm mock] [--cases benchmarks/cases]
```

Exit codes: 0 = SUCCESSFUL, 2 = any flagged status, 1 = error.

## 12. Outputs

Each run writes `outputs/<session_id>/`:
- `report.md`: dependency and versions, affected files, iterations, tests before/after, ruff before/after, final status, flag reason, citations used, git diff, unresolved failures.
- `report.json`: the same content in machine-readable form.
- `workspace/`: the git-tracked working copy, with one commit per iteration.

## 13. Benchmarks

Five self-contained cases in `benchmarks/cases/`. Each case directory:

```
case_XX_name/
  repo/              tiny project: src/, tests/, requirements.txt pinning the OLD version
  fake_lib/          old_version/ and new_version/ stub packages
  docs/              1-3 migration documents
  case.yaml          dependency, from_version, to_version, expected_status, expected_diagnosis, expected_min_tests_fixed
  plan.json          reference plan used by MockLLM
  patch.txt          reference SEARCH/REPLACE patch used by MockLLM
```

| Case | Change | Expected status | Expected diagnosis |
|------|--------|-----------------|--------------------|
| 01_rename | `old_api` renamed to `new_api` | SUCCESSFUL | - |
| 02_param_removed | keyword `mode` removed | SUCCESSFUL | - |
| 03_import_path | `lib.utils` moved to `lib.core.helpers` | SUCCESSFUL | - |
| 04_behavior_change | return semantics changed, no documented replacement | REQUIRES_HUMAN_REVIEW | INSUFFICIENT_EVIDENCE |
| 05_unrelated_failure | a test fails from a pre-existing bug | FAILED or REQUIRES_HUMAN_REVIEW | UNRELATED |

Case 04 and 05 pass only if the system does not edit source to force success and does not edit the test file.

Metrics (computed by `benchmarks/run_eval.py`): migration success rate, initial and final test pass rate, average iterations, files modified, human intervention rate, unresolved failures, execution time. Definitions are in `docs/evaluation.md`.

Mock LLM: `llm/mock.py` returns the case's `plan.json` and `patch.txt` keyed by stage and iteration. Deterministic. Required for all tests and for `eval --llm mock`.

## 14. Build phases

Each phase ends with a commit named `feat(phaseX): ...` and must pass its verification before the next phase starts.

- **A. Skeleton:** pyproject.toml, src layout, config (pydantic-settings), db schema, state models, Makefile. Verify: `pip install -e ".[dev]"` and `automigrate --help` succeed.
- **B. Ingest:** scanner, dependency parser, AST analyzer, `scan` command. Verify: tests pass; `scan` on case_01 lists the usages.
- **C. RAG:** loaders, chunker, BM25 index, retriever, `index-docs`. Verify: exact identifier query returns the correct chunk.
- **D. Tools:** sandbox filesystem, pytest/ruff runners with JSON parsing, git operations. Verify: traversal and disallowed commands are rejected in tests.
- **E. Agents and LLM:** planner, patcher (SEARCH/REPLACE with uniqueness checks), diagnoser, LLM adapters, MockLLM. Verify: patcher tests cover no-match, multi-match, and multi-block cases.
- **F. Orchestrator:** `graph.py` implementing section 5, including baseline, rollback, and max-iteration paths. Verify: `automigrate migrate` on case_01 with `--llm mock` returns SUCCESSFUL, and the workspace git log shows one commit per iteration.
- **G. Benchmarks:** five cases plus `run_eval.py`. Verify: cases 01-03 succeed, 04 and 05 flag as specified in section 13.
- **H. UI and reports:** Streamlit app and report generation. Verify: `streamlit run app/ui.py` runs case_01.
- **I. Docs and quality:** README, docs/architecture.md, docs/evaluation.md, LICENSE, ruff clean, coverage >= 80%.

## 15. Acceptance criteria

- `make install && make test` passes with no network access.
- `make lint` passes.
- `automigrate eval --llm mock` writes `benchmarks/results/metrics.csv` and `results.md` with the expected outcomes in section 13.
- The original repo has the same checksum before and after a run.
- All safety rules in section 9 have tests.
- Coverage of `src/automigrate` is at least 80%.
- README quickstart commands were executed, not just written.

## 16. Working conventions for the agent

- Read this file before every phase.
- Commit at the end of each phase. Use conventional commit messages.
- Never edit files under `benchmarks/cases/*/` except to create them during Phase G. Once a case is created, do not change its expected outcome to make the run pass.
- Never weaken a test to make it pass. If a test is wrong, explain why in the commit message and fix it with a clearly described change.
- If blocked, stop, write the blocker to `BLOCKERS.md`, and propose the smallest fix. Do not fake results.
- Final message: file tree, test and coverage summary, eval table, any unmet acceptance criteria, and reproduction commands.