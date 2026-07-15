# Convenience commands. On Windows without `make`, use backend commands directly
# or run scripts\run.ps1 (see README).
PY ?= python
VENV = .venv
BIN = $(VENV)/bin

.PHONY: help venv install run test lint docker-build docker-run clean

help:
	@echo "Targets: venv install run test docker-build docker-run clean"

venv:
	$(PY) -m venv $(VENV)

install: venv
	$(BIN)/pip install -U pip
	$(BIN)/pip install -r backend/requirements.txt

run:
	cd backend && ../$(BIN)/uvicorn main:app --reload --host 0.0.0.0 --port 8080

test:
	cd backend && ../$(BIN)/pytest -q

docker-build:
	docker build -f backend/Dockerfile -t nth-voice-gateway:latest .

docker-run:
	docker run --rm -p 8080:8080 -e DEFAULT_PROVIDER=mock nth-voice-gateway:latest

clean:
	rm -rf $(VENV) backend/.pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
