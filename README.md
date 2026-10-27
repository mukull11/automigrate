# AutoMigrate

AutoMigrate upgrades a Python repository from one version of a dependency to a
newer version. It detects the public API changes between the two released
versions, scans the repository for affected usages, and runs a test-guided repair
loop:

```
Clone -> API diff -> Usage scan -> Baseline -> Plan -> Patch -> Test -> Diagnose -> (loop | Done | Flag)
```

Validation results drive the next action. If the system cannot safely repair a
failure it stops and flags the repository for human review. It never forces a patch
onto the original repository.

## 60-second quickstart

Requires Windows PowerShell (or any shell), Python 3.11, `git`, `pip`, `ruff`, and
an API key for a live OpenAI-compatible model.

```powershell
python -m pip install -e ".[dev,ui]"
$env:OPENAI_API_KEY = "<your-key>"
$env:OPENAI_BASE_URL = "https://api.groq.com/openai/v1"

python -m automigrate migrate https://github.com/org/project --dependency requests --current 2.28.0 --target 2.32.0
```

Or migrate a local folder whose dependency is published on PyPI:

```powershell
python -m automigrate migrate C:\path\to\project --dependency requests --current 2.28.0 --target 2.32.0
```

Open the Streamlit UI:

```powershell
python -m streamlit run app/ui.py
```

`examples/rename_sample` is a known-broken regression fixture used by the tests.
Its `lib` dependency is local test data, not a PyPI package, so a live run against
it stops when pip cannot fetch a version.

The form asks for a repository folder path or GitHub URL, the dependency, the
current version, the target version, and a model.

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | yes | Bearer token for the live OpenAI-compatible endpoint. Never logged or persisted. |
| `OPENAI_BASE_URL` | no | Base URL of the endpoint (Groq, Google AI Studio, OpenAI, or a compatible gateway). |
| `AUTOMIGRATE_MODELS` | no | Comma-separated model chain override, e.g. `openai/gpt-oss-120b,gemini-2.5-flash`. |
| `GITHUB_TOKEN` | no | Read access token for private GitHub repositories. Never logged. |
| `AUTOMIGRATE_MAX_ITERATIONS` | no | Repair iterations, default `5`. |

The default model chain is:

1. `openai/gpt-oss-120b` (Groq)
2. `qwen/qwen3.8-27b` (Groq)
3. `gemini-2.5-flash` (Google)

`--model` (CLI) and the model dropdown (UI) select the first model; on a rejected
plan or patch, or an adapter error, AutoMigrate advances to the next model in the
chain. If no API key is set, the command exits with a message naming
`OPENAI_API_KEY` and `docs/real_llm.md`.

## What it does

1. **Clone safely.** A URL input is cloned into a temporary workspace. A local
   folder is copied. The user's original folder is never written to.
2. **Detect API changes.** The current and target versions are installed into
   separate temporary directories with `pip install --target`. Both package trees
   are parsed with `ast` to extract public functions, classes, methods, parameters,
   and module paths. The diff reports removed, added, renamed, moved, and
   parameter-changed symbols deterministically.
3. **Scan usages.** Every Python file in the repository is parsed to locate
   imports, calls, attributes, and keyword arguments of affected symbols.
4. **Retrieve documentation.** A docs folder supplied with `--docs` is indexed.
   README and CHANGELOG files shipped in the installed target package are indexed
   automatically.
5. **Repair with checks.** Baseline `pytest` and `ruff` run before any edit. Each
   iteration plans, patches with SEARCH/REPLACE blocks, then re-runs both. Bad
   JSON or non-applying patches get one retry, then the next model, then a flag.
   Test files are read-only to the agent.
6. **Report honestly.** When tests pass and ruff has no new issues the status is
   `SUCCESSFUL`. A repository without tests can only reach `UNVERIFIED`; it is
   never reported as successful without a passing test run.

## CLI

```text
automigrate scan REPOSITORY [--dependency NAME]
automigrate index-docs --docs DIR --dependency NAME
automigrate migrate REPOSITORY --dependency NAME --current VER --target VER
                      [--docs DIR] [--model MODEL] [--max-iterations 5] [--out outputs/]
automigrate report SESSION_ID
```

Exit codes: `0` = SUCCESSFUL, `2` = any flagged or unverified status, `1` = error.

## Tests and lint

```powershell
python -m ruff check src tests
python -m pytest -q
```

Tests never make network or live model calls. They use a fake model client and a
local API detector, both defined under `tests/`.

The helper script exposes the same tasks:

```powershell
.\scripts\tasks.ps1 lint
.\scripts\tasks.ps1 test
.\scripts\tasks.ps1 ui
```

## Safety

- The original repository is never modified. Local input is copied; URL input is
  cloned into a temporary directory that is removed after copying.
- Paths are resolved inside the workspace; traversal and escaping symlinks are
  rejected.
- External processes are limited to `pytest`, `ruff`, `git`, and `pip` with fixed
  argument lists, `shell=False`, and a timeout.
- Test files are read-only to the agent. Patches touching `tests/` or `test_*.py`
  are rejected.
- When a run ends `REQUIRES_HUMAN_REVIEW` or `FAILED`, the workspace is reset to the
  baseline commit so no unverified patch is left behind.
- API keys and `GITHUB_TOKEN` come from environment variables only and are never
  written to logs, the database, or reports.

## Limitations

- Public PyPI packages only. `pip install --target` must be able to fetch both
  versions; a private index or authenticated package fails with a clear error.
- `SUCCESSFUL` requires a passing test run. Repositories without tests are reported
  as `UNVERIFIED`.
- Free-tier model endpoints have rate limits and may be slow or temporarily
  unavailable; AutoMigrate falls back through the configured chain and then flags.
- Models are not deterministic. The same repository can produce different plans;
  the test and ruff checks are the only source of truth.
- Python only. Static extraction and SEARCH/REPLACE patching do not cover dynamic
  imports or generated code.
- The API diff compares installed packages, so a version that cannot be parsed with
  `ast` (for example a compiled-only wheel with no Python sources) stops the run
  with an error instead of guessing.

## Documentation

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/real_llm.md`](docs/real_llm.md)

## License

MIT. See [`LICENSE`](LICENSE).
