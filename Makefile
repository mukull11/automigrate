.PHONY: install test lint ui

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

lint:
	python -m ruff check src tests

ui:
	python -m streamlit run app/ui.py
