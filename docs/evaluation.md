# Evaluation

AutoMigrate ships five self-contained benchmark cases under `benchmarks/cases/`.
Each case contains a tiny repository, old and new stub versions of the dependency,
migration documentation, a reference plan and patch for the mock LLM, and a
`case.yaml` describing the expected outcome.

| Case                   | Change                                              | Expected status        | Expected diagnosis       |
|------------------------|-----------------------------------------------------|------------------------|--------------------------|
| `01_rename`            | `old_api` renamed to `new_api`                      | SUCCESSFUL             | -                        |
| `02_param_removed`     | keyword argument `mode` removed                     | SUCCESSFUL             | -                        |
| `03_import_path`       | `lib.utils` moved to `lib.core.helpers`             | SUCCESSFUL             | -                        |
| `04_behavior_change`   | return semantics changed, no documented replacement | REQUIRES_HUMAN_REVIEW  | INSUFFICIENT_EVIDENCE    |
| `05_unrelated_failure` | a test fails from a pre-existing bug                | REQUIRES_HUMAN_REVIEW  | UNRELATED                |

Cases 04 and 05 pass only if the system does not edit source to force success and
does not edit the test file.

## Running

```powershell
python -m automigrate eval --llm mock
```

Results are written to `benchmarks/results/metrics.csv` and
`benchmarks/results/results.md` (ignored by git). The mock LLM is deterministic, so
the suite is suitable for CI and offline runs.

## Per-case pass criteria

A case is `passed` when all of the following hold:

1. `actual_status` equals `expected_status`.
2. `actual_diagnosis` equals `expected_diagnosis` when one is declared.
3. The number of newly passing tests is at least `expected_min_tests_fixed`.
4. `tests_unchanged` is true (test files byte-identical to the original repo).
5. `repo_unchanged` is true (the original repository checksum is unchanged).
6. `honest_outcome` is true: a SUCCESSFUL case may have no unresolved failures; a
   flagged case must still report unresolved failures rather than pretending.
7. `clean_flag` is true: for expected `REQUIRES_HUMAN_REVIEW` or `FAILED`,
   `files_modified` must be 0 after the workspace rollback.

## Metrics

- **Migration success rate** - fraction of cases whose actual status is
  `SUCCESSFUL`.
- **Initial test pass rate** - baseline passed / (baseline passed + failed).
- **Final test pass rate** - final passed / (final passed + failed).
- **Average iterations** - mean iteration count reported by each run.
- **Files modified** - number of files that differ between the original repository
  and the final workspace, excluding git metadata and generated caches. Because
  flagged runs roll back to the baseline commit, cases 04 and 05 contribute 0;
  cases 01-03 contribute 1 each, for a total of 3.
- **Human intervention rate** - fraction of cases whose actual status is not
  `SUCCESSFUL`.
- **Unresolved failures** - total final failed + error tests across cases.
- **Execution time** - wall-clock seconds for the whole suite.

## Interpreting a flagged result

A flagged case means the system stopped instead of forcing a change it could not
validate. The workspace is reset to the baseline commit, `files_modified` is 0, and
the report keeps the flag reason and diagnosis. This is the intended behaviour for
cases 04 and 05: an honest stop is a pass for the benchmark, while a source edit left
behind is a failure.
