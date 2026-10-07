.PHONY: install run test check demo evaluate benchmark compose
install:
	python -m pip install -e '.[dev]'
run:
	uvicorn app.main:app --host 127.0.0.1 --port 8000
check:
	ruff check .
	ruff format --check .
	mypy app
test:
	pytest --cov=app --cov-report=term-missing
demo:
	python scripts/demo.py
evaluate:
	python scripts/evaluate.py
benchmark:
	python scripts/benchmark.py
compose:
	docker compose up --build -d --wait
