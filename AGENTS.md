# Project rules for AutoMigrate

## Git workflow (mandatory)
- After completing each build phase in BLUEPRINT.md, commit and push:
  git add -A
  git commit -m "feat(phaseX): <short summary>"
  git push origin main
- Commit small and often within a phase too, whenever a working unit passes its tests.
- Before every commit, run `git status` and confirm no secrets or generated files are staged.
- Never commit: .env, API keys, *.db, outputs/, workspace/, .venv/, __pycache__/.
- If `git push` fails, stop, show the full error, and wait for instructions. Do not force-push. Never use `git push --force`.

## Windows compatibility
- The developer runs Windows with PowerShell. Do not rely on `make`.
- Provide the same tasks as a `scripts/tasks.ps1` file (install, test, lint, ui) and in the README. A Makefile may also exist, but it is optional.
- Use `py -3.11` as the Python launcher in docs, and `python -m pytest` style commands in scripts.
- Use pathlib for all paths. Never hardcode `/` separators.
- Do not use shell features that only work on Linux.

## Working rules
- Read BLUEPRINT.md before each phase and follow it exactly.
- Verify each phase by running its commands before committing.
- Never weaken a test to make it pass.
- Never modify benchmark expected outcomes to make a run pass.