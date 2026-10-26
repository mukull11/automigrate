# Running a real model

AutoMigrate talks to any OpenAI-compatible chat completions endpoint through the
`--llm openai` adapter. The adapter reads these environment variables only:

- `OPENAI_API_KEY` - required; sent as a bearer token, never logged or persisted.
- `OPENAI_BASE_URL` - optional; point it at a non-OpenAI endpoint.
- `AUTOMIGRATE_MODEL` - optional; provider-agnostic model override.
- `AUTOMIGRATE_OPENAI_MODEL` - optional; OpenAI-specific fallback if
  `AUTOMIGRATE_MODEL` is unset.

Precedence for the model name: explicit argument, `AUTOMIGRATE_MODEL`,
`AUTOMIGRATE_OPENAI_MODEL`, then the built-in default.

The examples below use Google AI Studio's OpenAI-compatible endpoint. Replace the
placeholder with your own key. Do not paste a real key into a file or a commit.

## 1. Install the OpenAI extra

```powershell
python -m pip install -e ".[openai]"
```

## 2. Set the environment for the current PowerShell session

```powershell
$env:OPENAI_API_KEY = "<your-google-ai-studio-key>"
$env:OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
$env:AUTOMIGRATE_MODEL = "gemini-2.5-flash"
```

To clear the key when you are done:

```powershell
Remove-Item Env:OPENAI_API_KEY
```

## 3. Run the successful case (01_rename)

```powershell
python -m automigrate migrate benchmarks\cases\01_rename\repo --dependency lib --from 1.0.0 --to 2.0.0 --docs benchmarks\cases\01_rename\docs --llm openai --out outputs
```

Copy the `Session:` value from the output and render the report:

```powershell
python -m automigrate report <session_id>
```

Expected: `Status: SUCCESSFUL` (exit code 0), with a diff to `src/app.py` in the
report.

## 4. Run the case that should stop (04_behavior_change)

```powershell
python -m automigrate migrate benchmarks\cases\04_behavior_change\repo --dependency lib --from 1.0.0 --to 2.0.0 --docs benchmarks\cases\04_behavior_change\docs --llm openai --out outputs
```

Expected: `Status: REQUIRES_HUMAN_REVIEW` (exit code 2). The workspace is rolled
back to the baseline commit, so the report shows `Rolled back to baseline: yes`
and any discarded diff is labelled as unverified.

## Notes

- A real model is not deterministic. It may produce an invalid plan or a patch
  that does not apply; the state machine then flags the case instead of forcing a
  change.
- The API key is read from the environment inside the adapter. It is not written
  to SQLite, `report.md`, `report.json`, or the workspace diff.
- The mock LLM (`--llm mock`) remains the reproducible option for the benchmark
  suite and tests.
