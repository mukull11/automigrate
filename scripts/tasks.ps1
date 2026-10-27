param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "test", "lint", "ui")]
    [string]$Task = "test"
)

$ErrorActionPreference = "Stop"

switch ($Task) {
    "install" {
        python -m pip install -e ".[dev]"
    }
    "test" {
        python -m pytest
    }
    "lint" {
        python -m ruff check src tests
    }
    "ui" {
        python -m streamlit run app/ui.py
    }
}
