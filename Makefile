.PHONY: install test test-duckdb lint fixtures serve docs build clean

install:
	pip install -e ".[dev]"

test:
	pytest --cov=adv_data_comp --cov-report=term-missing --cov-report=html

test-duckdb:
	ADV_DATA_COMP_MEMORY_THRESHOLD_MB=0 pytest --cov=adv_data_comp --cov-report=term-missing

lint:
	ruff check .
	black --check .
	mypy adv_data_comp cli

fixtures:
	python -m adv_data_comp.dev.generate_fixtures $(ARGS)

serve:
	uvicorn adv_data_comp.serve.app:app --reload

docs:
	mkdocs build

build:
	python -m build

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
