# Architecture

AutoMigrate is a local Python CLI with a Streamlit demo front end. It is organised
in layers; higher layers may import lower layers, but tools and RAG never import
agent or graph code.

```
Interfaces   cli.py (Typer)            app/ui.py (Streamlit)
                 \                        /
Auto mode          auto.py: discover -> triage -> per-dependency runs
                 \                        /
Orchestrator        graph.py state machine
                 /        |         \
Agents      planner.py  patcher.py  diagnoser.py  triage.py
                 \        |         /
API diff      pip install -> ast symbols -> deterministic diff -> usage scan
RAG           loaders -> chunker -> index -> retriever
Tools         filesystem, test_runner, lint_runner, git, subprocess (pip)
Persistence   db.py (SQLite)
LLM adapters  openai-compatible model chain (one interface)
```

Both interfaces call `auto.run_auto_migration()`, which owns dependency discovery
and triage and then calls `graph.run_migration()` once per selected dependency.
They contain no pipeline logic.

## Autonomous workflow

1. **Resolve the source once.** A GitHub URL is cloned once into a temporary
   folder and reused for discovery and every dependency run. A local folder is
   used in place; `run_migration` still copies it into a workspace.
2. **Discover.** `ingest/versions.py` parses declarations with
   `detect_declarations`, asks `https://pypi.org/pypi/{name}/json` for the latest
   stable release (pre-releases and yanked files are skipped), compares versions
   with `packaging.version.Version`, and counts usages with `scan_repository`.
   Unpinned dependencies fall back to `importlib.metadata.version`. Missing,
   private, or invalid packages are marked `unresolved` rather than guessed.
3. **Triage.** `agent/triage.py` sends the candidates to the LLM and validates the
   returned dependency names and versions against the candidate set. Invalid or
   duplicate output is retried once, then a deterministic usage-ranked fallback
   is used. `max_dependencies` (0 = all) caps the selection.
4. **Migrate.** Each selected dependency calls the unchanged
   `graph.run_migration()` with explicit current/target versions and a fresh
   session and workspace. A failed dependency does not stop the others. Progress
   messages are prefixed with `[dependency]` for the activity panel.

The LLM triages; the deterministic detector verifies the actual API changes;
tests decide whether the patch is accepted.

## State machine

States: `INGEST`, `DETECT`, `RETRIEVE`, `PLAN`, `PATCH`, `VALIDATE`, `DIAGNOSE`,
`REPORT`, plus terminal `DONE` and `FLAGGED`.

| From     | Condition                                                        | To                     |
|----------|------------------------------------------------------------------|------------------------|
| start    | baseline validation as iteration 0                               | INGEST                 |
| INGEST   | Python files exist and dependency is declared                    | DETECT                 |
| INGEST   | dependency not declared                                          | FLAGGED                |
| DETECT   | API diff computed and at least one usage found                   | RETRIEVE               |
| DETECT   | zero usages                                                      | FLAGGED                |
| RETRIEVE | evidence above threshold, or an API diff with changes            | PLAN                   |
| RETRIEVE | no evidence                                                      | FLAGGED                |
| PLAN     | valid plan with a change of confidence >= 0.5                    | PATCH                  |
| PLAN     | invalid plan or no confident change                              | FLAGGED                |
| PATCH    | all SEARCH/REPLACE blocks apply                                  | VALIDATE               |
| PATCH    | a block fails after one retry                                    | FLAGGED                |
| VALIDATE | always                                                           | DIAGNOSE               |
| DIAGNOSE | tests pass and no new ruff issues                                | REPORT -> DONE/UNVERIFIED |
| DIAGNOSE | migration-related failure, iteration < max                       | PLAN (iteration + 1)   |
| DIAGNOSE | unrelated / insufficient evidence, or max iterations reached     | REPORT -> FLAGGED      |
| DIAGNOSE | pass count dropped                                               | roll back, REPORT -> FLAGGED |

Each transition writes one `agent_action` row. The iteration counter increments only
when re-entering PLAN from DIAGNOSE. The default `max_iterations` is 5.

## Workspace lifecycle

1. `prepare_workspace` copies the repository into
   `<outputs>/<session_id>/workspace`, initialises git, and commits
   `automigrate: baseline`. On Windows `<outputs>` defaults to
   `D:\AutoMigrateOutputs`; override it with `AUTOMIGRATE_OUTPUTS_DIR`. Generated
   caches (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`) are
   excluded via `.git/info/exclude` so they never end up in a migration commit.
2. Each successful PATCH commits `automigrate: iter {n} - {summary}`.
3. If a pass count drops during a repair iteration, the workspace is reset to the
   commit before that patch.
4. When the final status is `REQUIRES_HUMAN_REVIEW` or `FAILED`, the workspace is
   reset to the baseline commit before the report row is written. No unverified
   patch remains.

A GitHub URL is cloned into a temporary directory once per auto run; each
dependency run copies that clone into its own workspace. The clone is removed
when the run ends. Private clones may use `GITHUB_TOKEN`, which is redacted from
errors and never persisted. The original repository is never written to.

## Outputs and database

Runtime state lives outside the project folder. On Windows the default is
`D:\AutoMigrateOutputs`, falling back to `%LOCALAPPDATA%\AutoMigrate\outputs`
when the drive is unavailable. `AUTOMIGRATE_OUTPUTS_DIR` overrides the folder.
The default database is `<outputs>\automigrate.db`; `AUTOMIGRATE_DB` overrides it.
`automigrate report <session-id>` reads that default database, and `--db` reads a
legacy or alternate database. The existing `automigrate.db` in the project root is
left untouched.

## API diff

The current and target versions of the dependency are installed with
`pip install --target` into separate temporary directories. Each package tree is
parsed with `ast` to extract public functions, classes, methods, parameters, and
module paths. The diff deterministically classifies removed, added, renamed
(similar name and identical signature), moved (same symbol in a different module),
and parameter-changed symbols. Every Python file in the repository is then parsed to
find imports, calls, attributes, and keyword arguments of affected symbols. The
diff and usage locations are included in the planner prompt together with the
baseline/latest test and ruff output.

If pip cannot fetch either version, the run stops with a clear error. It never
guesses a diff. README and CHANGELOG files shipped inside the target package are
chunked and indexed alongside an optional user docs folder.

## RAG

Documentation is chunked by headings and paragraphs (about 600 tokens, 80-token
overlap) and stored in SQLite. `chunk_id = sha1(source_doc + text)`. Retrieval uses
BM25 keyword search over identifier-preserving tokens; optional semantic search is
fused with reciprocal rank fusion when `sentence-transformers` is installed. BM25
alone is sufficient. The query contains the dependency, both versions, every
detected symbol, and (from iteration 2) the head of the latest traceback.

Plans may cite only chunk IDs retrieved in the current iteration. Unknown citations
make the plan invalid before any patch is attempted. When an automatic API diff is
available, a plan may proceed with no documentation citations.

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
- Subprocess allowlist: `pytest`, `ruff`, `git`, `pip`, fixed arguments,
  `shell=False`, and a timeout.
- LLM output is untrusted: strict parsers plus pydantic validation before use.
- Secrets come from environment variables only and are never persisted.
- A repository without tests can only reach `UNVERIFIED` after ruff and an import
  check; `SUCCESSFUL` always requires a passing test run.
