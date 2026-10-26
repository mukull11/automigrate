.PHONY: install test lint eval ui

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

lint:
	python -m ruff check src tests

eval:
	python -m automigrate eval --llm mock

ui:
	python -m streamlit run src/automigrate/app/ui.py
