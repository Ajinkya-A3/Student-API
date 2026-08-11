SHELL := /bin/bash

PYTHON      ?= python3.14
VENV        := .venv
VENV_BIN    := $(VENV)/bin
PIP         := $(VENV_BIN)/pip
UVICORN     := $(VENV_BIN)/uvicorn
ALEMBIC     := $(VENV_BIN)/alembic
PYTEST      := $(VENV_BIN)/pytest

APP_MODULE  := app.main:app
HOST        ?= 0.0.0.0
PORT        ?= 8000

# docker-compose.yaml lives at the repo root, one level up from student-api/
# where this Makefile is run from.
COMPOSE_FILE := ../docker-compose.yaml

.DEFAULT_GOAL := help

.PHONY: help venv install build db-up db-down db-logs migrate migrate-down \
        revision run dev test test-cov lint clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtual environment
	$(PYTHON) -m venv $(VENV)

install: venv ## Create venv and install all dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

build: install ## Alias for install (satisfies "build" step)

db-up: ## Start the Postgres container in the background and wait until it's healthy
	docker compose -f $(COMPOSE_FILE) up -d postgres
	docker compose -f $(COMPOSE_FILE) exec postgres sh -c \
		'until pg_isready -U postgres -d student_db > /dev/null 2>&1; do sleep 1; done'
	@echo "Postgres is ready."

db-down: ## Stop and remove the Postgres container (data volume is preserved)
	docker compose -f $(COMPOSE_FILE) stop postgres

db-logs: ## Tail the Postgres container's logs
	docker compose -f $(COMPOSE_FILE) logs -f postgres

migrate: ## Apply all pending Alembic migrations (upgrade to head)
	$(ALEMBIC) upgrade head

migrate-down: ## Roll back the last Alembic migration
	$(ALEMBIC) downgrade -1

revision: ## Autogenerate a new Alembic revision. Usage: make revision m="add index"
	$(ALEMBIC) revision --autogenerate -m "$(m)"

run: ## Run the API with production-style uvicorn (no reload)
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT)

dev: db-up ## Start Postgres (if not already up), then run the API locally with hot-reload
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

test: ## Run the unit test suite
	$(PYTEST) -v

test-cov: ## Run tests with coverage report
	$(PYTEST) --cov=app --cov-report=term-missing

lint: ## Install flake8 and run linting
   	$(PIP) install flake8
   	$(VENV_BIN)/flake8 app

clean: ## Remove venv, caches and pyc files
	rm -rf $(VENV) .pytest_cache .coverage
	find . -type d -name '__pycache__' -exec rm -rf {} +