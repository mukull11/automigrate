# AutoMigrate

AutoMigrate detects dependency API changes in a Python repository and, when it can prove a fix with tests, proposes a patch for the affected code. It never edits your original repository. All changes happen in a temporary working copy, and the result is a report you can review.

```
Clone -> API diff -> Usage scan -> Baseline -> Plan -> Patch -> Test -> Diagnose -> (loop | Done | Flag)
```

Test results drive the next step. When the system cannot safely repair a failure, it stops and reports why.

Repository: https://github.com/mukull11/automigrate

## What it does

1. **Clone:** accepts a local folder or a GitHub URL and copies it into a temporary workspace.
2. **API diff:** installs the current and target versions of the dependency into separate temporary folders with `pip install --target`, then compares their public names using `ast`. Each candidate name is also checked at runtime in both versions in a separate subprocess. This catches names removed through package internals, such as `numpy.float_`.
3. **Usage scan:** finds every use of a changed name in the repository, with file and line.
4. **Retrieve documentation:** indexes a docs folder passed with `--docs`, plus the README and CHANGELOG shipped with the installed target package. Retrieval uses keyword search (BM25).
5. **Baseline:** runs `pytest` and `ruff` before any change.
6. **Plan, patch, test:** the LLM proposes SEARCH/REPLACE edits, which must match the file exactly once. Tests and ruff are rerun after each patch. Test files are read-only to the agent.
7. **Diagnose:** on failure, the run retries, moves to the next model of the same provider, rolls back to baseline, or stops for human review.
8. **Report:** writes `report.md` and `report.json` with the status, detected changes, every edit with its file and line, model attempts, and test results before and after.

## Status meanings

| Status | Meaning |
|---|---|
| `SUCCESSFUL` | Tests pass after the patch and ruff has no new issues. |
| `UNVERIFIED` | The repository has no tests, so no fix can be proven. |
| `REQUIRES_HUMAN_REVIEW` | The tool stopped safely. Examples: no usages of the changed API, or no documented replacement (see `--allow-undocumented`). |
| `FAILED` | An error occurred, such as a provider returning 401 or 404. The workspace is reset to baseline. |

## Requirements

- Windows PowerShell (or any shell), Python 3.11 or newer, and `git`
- A key for at least one supported provider (see below)
- A dependency published on PyPI, with both versions installable

## Quickstart

```powershell
git clone https://github.com/mukull11/automigrate.git
cd automigrate
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ui]"
python -m pytest -q
```

Run a migration. Set the key in the same window you run the command in:

```powershell
$env:GROQ_API_KEY = "<your-key>"
python -m automigrate migrate https://github.com/mukull11/numpy-migration-test.git --dependency numpy --current 1.24.0 --target 2.0.0 --provider groq --model openai/gpt-oss-120b
```

Or migrate a local folder:

```powershell
python -m automigrate migrate C:\path\to\project --dependency numpy --current 1.24.0 --target 2.0.0 --provider groq --model openai/gpt-oss-120b
```

Open the Streamlit UI:

```powershell
python -m streamlit run app/ui.py
```

Use the URL Streamlit prints, usually `http://localhost:8501`. Keep the UI on localhost, since the API key field is only safe on a machine you control.

## Providers and API keys

Each provider has its own key variable. Set only the one you use.

| Provider id | Name | Key variable |
|---|---|---|
| `gemini` | Google Gemini | `GEMINI_API_KEY` |
| `openai` | OpenAI | `OPENAI_API_KEY` |
| `anthropic` | Anthropic Claude | `ANTHROPIC_API_KEY` |
| `groq` | Groq | `GROQ_API_KEY` |
| `qwen` | Alibaba DashScope (Qwen) | `DASHSCOPE_API_KEY` |
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` |
| `xai` | xAI (Grok) | `XAI_API_KEY` |
| `mistral` | Mistral | `MISTRAL_API_KEY` |
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` |
| `custom` | Any OpenAI-compatible endpoint | `AUTOMIGRATE_CUSTOM_API_KEY` |

Keys are resolved in this order: the key entered in the UI or passed with `--api-key`, then the provider's environment variable. If neither exists, the run stops before doing any work and names the variable to set.

Model names belong to their provider. Choose a model that the selected provider serves. A mismatch, such as a Gemini model sent to Groq, returns HTTP 404. The UI model list comes from each provider's list-models endpoint, and you can also type a model ID by hand.

Fallback stays within one provider. On 404 or 429, AutoMigrate tries the next model of the same provider. It never switches provider unless you choose a different one.

## Options

| Option | Purpose |
|---|---|
| `--provider` | Provider id from the table above. |
| `--model` | Model ID for that provider. |
| `--api-key` | Overrides the provider's environment variable for this run only. It stays in shell history, so prefer the environment variable. |
| `--allow-undocumented` | Accept a confident planned change even when the docs do not name its replacement. Off by default. |
| `--no-allow-undocumented` | Explicitly keep the default. |
| `--docs DIR` | Folder of migration docs to index. |
| `--max-iterations N` | Repair iterations, default 5. |
| `--out DIR` | Output folder, default `outputs\`. |

| Environment variable | Purpose |
|---|---|
| `AUTOMIGRATE_MODELS` | Comma-separated model chain override for the selected provider. |
| `AUTOMIGRATE_ALLOW_UNDOCUMENTED` | `true` or `false`. The CLI flag takes precedence. |
| `AUTOMIGRATE_MAX_ITERATIONS` | Repair iterations, default 5. |
| `OPENAI_BASE_URL` | Base URL for the OpenAI-compatible endpoint. Set it to match the provider you use. |
| `GITHUB_TOKEN` | Read access to private GitHub repositories. Never logged. |

Run `python -m automigrate report <session-id>` to rebuild a report for any past run. Session IDs are the folder names under `outputs\`.

## Demo

A small public fixture that calls a function removed in numpy 2.0:

```powershell
$env:GROQ_API_KEY = "<your-key>"
python -m automigrate migrate https://github.com/mukull11/numpy-migration-test.git --dependency numpy --current 1.24.0 --target 2.0.0 --provider groq --model openai/gpt-oss-120b --allow-undocumented
```

Expected result: the detector reports `numpy.float_` as removed, the planner proposes `np.float_(x)` to `np.float64(x)`, the tests pass, and the status is `SUCCESSFUL`. The original fixture on GitHub is not changed. The edited copy is in `outputs\<session-id>\workspace`.

Without `--allow-undocumented`, the same run stops with `REQUIRES_HUMAN_REVIEW` before patching. That is the default safety behaviour.

## Safety

- The original repository is never modified. Local input is copied; URL input is cloned into a temporary folder.
- All paths are resolved inside the workspace. Path traversal and symlinks that point outside are rejected.
- External processes are limited to `git`, `pip`, `pytest`, and `ruff`, with fixed argument lists, `shell=False`, and timeouts.
- Test files are read-only to the agent. Patches touching `tests/` or `test_*.py` are rejected.
- Patches must match exactly once. Unmatched or ambiguous patches are rejected.
- When a run ends `REQUIRES_HUMAN_REVIEW` or `FAILED`, the workspace is reset to baseline.
- API keys and `GITHUB_TOKEN` are read from environment variables only. They are never written to logs, the database, or reports.

## Limitations

- **Detection is limited to public names of installable packages.** Behaviour changes, changed defaults, configuration changes, and dynamic imports are not detected. The runtime name check catches names that exist in one version and not the other, but not changes that keep the same name.
- **Undocumented replacements are opt-in.** With `--allow-undocumented`, the planner can apply a change its docs do not cover. The tests are the only check, so a wrong edit can pass a weak test suite. Use it only on repositories with meaningful tests.
- **`SUCCESSFUL` requires a passing test run.** Repositories without tests are `UNVERIFIED`.
- **Private repositories and private packages** need `GITHUB_TOKEN` for cloning, and private packages are not supported.
- **Free model tiers are rate-limited** (HTTP 429) and sometimes unavailable (HTTP 503). A run can stop for this reason. It is reported as `FAILED` with the provider's error, which says nothing about the code.
- **Models are not deterministic.** The same repository can produce different plans on different days.
- **Large, multi-file migrations** may exceed what one planning step can handle.
- **The planner can be called more than once** for the same input. This is a known issue.
- **Retrieval is keyword-based (BM25) with a score threshold of 0.0**, so retrieved documents are not filtered by relevance. Semantic embeddings are optional and not enabled by default.
- **Python packages only.** Non-Python or non-pip dependencies are out of scope.

## Project layout

- `src/automigrate/`: pipeline, API diff, usage scan, planner, patcher, LLM adapters, report writer
- `app/ui.py`: Streamlit interface
- `examples/rename_sample/`: small regression fixture used by the tests
- `docs/architecture.md`: pipeline and state machine
- `docs/real_llm.md`: provider setup

## Tests and lint

```powershell
python -m ruff check src tests app
python -m pytest -q
```

Tests never make network or live model calls. They use a fake model client and a local fake package.

## Documentation

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/real_llm.md`](docs/real_llm.md)

## License

MIT. See [`LICENSE`](LICENSE).