.PHONY: help up down logs build rebuild restart ps \
	shell api-shell db-shell redis-shell \
	install lint lint-fix format typecheck test test-cov \
	migrate migrate-new migrate-down migrate-history \
	clean

COMPOSE := docker compose
PY := python

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------- Docker stack ----------
up: ## Start the whole stack
	$(COMPOSE) up -d

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

build: ## Build service images
	$(COMPOSE) build

rebuild: ## Rebuild API image from scratch
	$(COMPOSE) build --no-cache api

restart: ## Restart the API container
	$(COMPOSE) restart api

ps: ## List running services
	$(COMPOSE) ps

# ---------- Shells ----------
shell: api-shell ## Alias for api-shell

api-shell: ## Bash into the API container
	$(COMPOSE) exec api bash

db-shell: ## psql into postgres
	$(COMPOSE) exec postgres psql -U spendlens -d spendlens

redis-shell: ## redis-cli into redis
	$(COMPOSE) exec redis redis-cli

# ---------- Python dev tooling (run inside backend/) ----------
install: ## Install Python deps locally with the dev extras
	cd backend && pip install -e ".[dev]"

lint: ## Run ruff
	cd backend && ruff check .

lint-fix: ## Run ruff with --fix
	cd backend && ruff check --fix .

format: ## Format code with ruff
	cd backend && ruff format .

typecheck: ## Run mypy
	cd backend && mypy app

test: ## Run pytest inside the API container
	$(COMPOSE) exec api pytest

test-cov: ## Run pytest with coverage
	$(COMPOSE) exec api pytest --cov=app --cov-report=term-missing

# ---------- Migrations ----------
migrate: ## Apply all pending migrations
	$(COMPOSE) exec api alembic upgrade head

migrate-new: ## Generate a new migration. Usage: make migrate-new msg="add foo"
	@if [ -z "$(msg)" ]; then echo "Usage: make migrate-new msg=\"description\""; exit 1; fi
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(msg)"

migrate-down: ## Roll back the most recent migration
	$(COMPOSE) exec api alembic downgrade -1

migrate-history: ## Show migration history
	$(COMPOSE) exec api alembic history

# ---------- Housekeeping ----------
clean: ## Stop stack and remove volumes
	$(COMPOSE) down -v
