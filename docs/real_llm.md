# Running a live model

AutoMigrate has no mock mode in the product. Every migration uses a live
OpenAI-compatible chat completions endpoint. The key is resolved once, in this
order:

1. An explicit key: the Streamlit `API key` field or the CLI `--api-key` flag.
2. `OPENAI_API_KEY` from the environment.

The adapter reads these environment variables:

- `OPENAI_API_KEY` - required unless an explicit key is supplied; sent as a bearer
  token, never logged or persisted.
- `OPENAI_BASE_URL` - optional; point it at a non-OpenAI endpoint.
- `AUTOMIGRATE_MODELS` - optional; comma-separated model chain.
- `AUTOMIGRATE_MODEL` - optional; single-model override for the low-level adapter.
- `GITHUB_TOKEN` - optional; read access for private GitHub URLs. Never logged.

If no key is supplied, the CLI exits with code 1 and a message naming
`OPENAI_API_KEY` and this document. There is no fallback to a fake or local model.

## Model chain

The default chain is:

1. `openai/gpt-oss-120b` (Groq)
2. `qwen/qwen3.8-27b` (Groq)
3. `gemini-2.5-flash` (Google)

`--model` (or the UI dropdown) chooses the first model. AutoMigrate advances to the
next model when an adapter raises an error or when a plan/patch is still invalid
after one retry with the error message. When the chain is exhausted the run is
flagged. Override the whole chain with:

```powershell
$env:AUTOMIGRATE_MODELS = "openai/gpt-oss-120b,gemini-2.5-flash"
```

The model names are sent verbatim. `OPENAI_BASE_URL` selects the endpoint; the
CLI does not read a separate key per vendor.

## 1. Install the OpenAI extra

```powershell
python -m pip install -e ".[openai,ui]"
```

## 2. Set the environment for the current PowerShell session

Groq example:

```powershell
$env:OPENAI_API_KEY = "<your-groq-key>"
$env:OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
```

Google AI Studio example:

```powershell
$env:OPENAI_API_KEY = "<your-google-ai-studio-key>"
$env:OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
```

To clear the key when you are done:

```powershell
Remove-Item Env:OPENAI_API_KEY
```

## 3. Migrate a repository

```powershell
python -m automigrate migrate https://github.com/org/project --provider groq --model openai/gpt-oss-120b
```

The command discovers every declared dependency, resolves each one's latest
stable version from PyPI, and asks the selected model which dependencies to
migrate first. Triage is advisory: if the model recommends none while outdated
dependencies exist, AutoMigrate falls back to a deterministic usage-ranked
selection. Only zero-usage candidates can be skipped. The deterministic API diff
decides what actually changed, and the repository's tests decide whether a patch
is accepted.
Use `--max-dependencies N` to cap the number of attempted dependencies (the
default, `0`, attempts all outdated ones). Reports and workspaces are written under
`D:\AutoMigrateOutputs` on Windows, or the folder set by
`AUTOMIGRATE_OUTPUTS_DIR`.

The current and target versions must be installable from PyPI. The bundled
`examples/rename_sample` fixture uses a local `lib` stub, so it is only used by the
offline tests and cannot be migrated live without a real published package. AutoMigrate runs
`pip install --target` for each version, parses both package trees with `ast`, and
feeds the deterministic diff and the repository usage scan to the planner.

Copy a `Session:` value from the per-dependency output and render the report:

```powershell
python -m automigrate report <session_id>
```

Reports and workspaces are under `D:\AutoMigrateOutputs` on Windows (override with
`AUTOMIGRATE_OUTPUTS_DIR`). `report` reads `<outputs>\automigrate.db`; pass
`--db automigrate.db` to read a legacy database in the project root.

## 4. Private repositories

Export a token with read access before running the command:

```powershell
$env:GITHUB_TOKEN = "<your-token>"
python -m automigrate migrate https://github.com/org/private-project
```

The token is injected into the temporary clone URL and redacted from git errors. It
is never written to the database, reports, or the workspace.

## Notes

- A real model is not deterministic. It may produce an invalid plan or a patch that
  does not apply; the state machine retries once, then moves to the next model, then
  flags the run instead of forcing a change.
- The API key is read from the environment inside the adapter. It is not written to
  SQLite, `report.md`, `report.json`, or the workspace diff.
- Automated tests use a fake model client defined under `tests/`; it is not
  importable from the CLI.
