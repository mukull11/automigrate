# Architecture

AutoMigrate is a local Python CLI with a Streamlit demo front end. It is organised
in layers; higher layers may import lower layers, but tools and RAG never import
agent or graph code.

```
Interfaces   cli.py (Typer)            app/ui.py (Streamlit)
                 \                        /
Orchestrator        graph.py state machine
                 /        |         \
Agents      planner.py  patcher.py  diagnoser.py
                 \        |         /
RAG           loaders -> chunker -> index -> retriever
Tools         filesystem, test_runner, lint_runner, git, subprocess
Persistence   db.py (SQLite)
LLM adapters  anthropic, openai, mock (one interface)
```

Both interfaces call `graph.run_migration()` and contain no pipeline logic.

## State machine

States: `INGEST`, `DETECT`, `RETRIEVE`, `PLAN`, `PATCH`, `VALIDATE`, `DIAGNOSE`,
`REPORT`, plus terminal `DONE` and `FLAGGED`.

| From     | Condition                                                        | To                     |
|----------|------------------------------------------------------------------|------------------------|
| start    | baseline validation as iteration 0                               | INGEST                 |
| INGEST   | Python files exist and dependency is declared                    | DETECT                 |
| INGEST   | dependency not declared                                          | FLAGGED                |
| DETECT   | at least one usage found                                         | RETRIEVE               |
| DETECT   | zero usages                                                      | FLAGGED                |
| RETRIEVE | evidence above threshold                                         | PLAN                   |
| RETRIEVE | no evidence                                                      | FLAGGED                |
| PLAN     | valid plan with a change of confidence >= 0.5                    | PATCH                  |
| PLAN     | invalid plan or no confident change                              | FLAGGED                |
| PATCH    | all SEARCH/REPLACE blocks apply                                  | VALIDATE               |
| PATCH    | a block fails after one retry                                    | FLAGGED                |
| VALIDATE | always                                                           | DIAGNOSE               |
| DIAGNOSE | tests pass and no new ruff issues                                | REPORT -> DONE         |
| DIAGNOSE | migration-related failure, iteration < max                       | PLAN (iteration + 1)   |
| DIAGNOSE | unrelated / insufficient evidence, or max iterations reached     | REPORT -> FLAGGED      |
| DIAGNOSE | pass count dropped                                               | roll back, REPORT -> FLAGGED |

Each transition writes one `agent_action` row. The iteration counter increments only
when re-entering PLAN from DIAGNOSE. The default `max_iterations` is 5.

## Workspace lifecycle

1. `prepare_workspace` copies the repository into `outputs/<session_id>/workspace`,
   initialises git, and commits `automigrate: baseline`. Generated caches
   (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`) are excluded via
   `.git/info/exclude` so they never end up in a migration commit.
2. Each successful PATCH commits `automigrate: iter {n} - {summary}`.
3. If a pass count drops during a repair iteration, the workspace is reset to the
   commit before that patch.
4. When the final status is `REQUIRES_HUMAN_REVIEW` or `FAILED`, the workspace is
   reset to the baseline commit before the report row is written. No unverified
   patch remains.

The original repository is never written to.

## RAG

Documentation is chunked by headings and paragraphs (about 600 tokens, 80-token
overlap) and stored in SQLite. `chunk_id = sha1(source_doc + text)`. Retrieval uses
BM25 keyword search over identifier-preserving tokens; optional semantic search is
fused with reciprocal rank fusion when `sentence-transformers` is installed. BM25
alone is sufficient. The query contains the dependency, both versions, every
detected symbol, and (from iteration 2) the head of the latest traceback.

Plans may cite only chunk IDs retrieved in the current iteration. Unknown citations
make the plan invalid before any patch is attempted.

## Patch format

```
FILE: relative/path/to/file.py
<<<<<<< SEARCH
exact existing lines, copied verbatim
=======
replacement lines
>>>>>>> REPLACE
```

The SEARCH text must occur exactly once. Blocks are applied in order; if any block
fails, no partial write persists. Test files are read-only.

## Persistence

SQLite (default `./automigrate.db`) records repositories, dependencies, sessions,
source-file hashes, every agent action, every test run, every migration change with
its diff and commit SHA, indexed document chunks, and the final report row. All SQL
lives in `db.py`.

## Safety

- Workspace-confined path resolution with traversal and symlink rejection.
- Subprocess allowlist: `pytest`, `ruff`, `git`, fixed arguments, `shell=False`,
  120-second default timeout.
- LLM output is untrusted: strict parsers plus pydantic validation before use.
- Secrets come from environment variables only and are never persisted.
