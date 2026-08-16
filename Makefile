.PHONY: install cims demo serve test lint typecheck eval clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -q -r requirements.txt
	@echo "installed. next: cp .env.example .env && edit, then make demo"

cims:
	$(PY) -m scripts.generate_cims

demo: cims
	$(PY) -m scripts.run_demo

serve:
	$(VENV)/bin/uvicorn app.main:app --reload --port 8000

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check app scripts evals tests
	$(VENV)/bin/ruff format --check app scripts evals tests

typecheck:
	$(VENV)/bin/mypy app

eval:
	$(PY) -m evals.run_evals

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache **/__pycache__ *.db
