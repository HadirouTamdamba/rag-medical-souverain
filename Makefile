.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON ?= python3
VENV := backend/.venv
BIN := $(VENV)/bin
COMPOSE_PROD := docker compose -f docker-compose.yml
API ?= http://localhost:8000

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Local development ------------------------------------------------------

.PHONY: install
install: ## Create the backend venv and install dependencies (runtime + tooling)
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
	cd frontend && npm ci

.PHONY: test
test: ## Run the backend test suite
	cd backend && ../$(BIN)/pytest

.PHONY: lint
lint: ## Ruff (backend) and ESLint (frontend)
	cd backend && ../$(BIN)/ruff check .
	cd frontend && npm run lint

.PHONY: fmt
fmt: ## Apply Ruff autofixes
	cd backend && ../$(BIN)/ruff check --fix .

.PHONY: check
check: lint test ## Lint + tests

# --- Docker -----------------------------------------------------------------

.PHONY: up
up: ## Start the stack in development mode (bind mounts, ports published)
	docker compose up -d --build

.PHONY: up-prod
up-prod: ## Start the stack in production configuration
	$(COMPOSE_PROD) up -d --build

.PHONY: down
down: ## Stop the stack (volumes are preserved)
	docker compose down

.PHONY: destroy
destroy: ## Stop the stack AND delete volumes (Qdrant index, models)
	docker compose down -v

.PHONY: logs
logs: ## Follow the backend logs
	docker compose logs -f backend

.PHONY: build
build: ## Build the images without starting them
	$(COMPOSE_PROD) build

# --- Operations -------------------------------------------------------------

.PHONY: health
health: ## API liveness
	@curl -fsS -m 5 $(API)/health && echo

.PHONY: ready
ready: ## Readiness (Qdrant + Ollama + model pulled)
	@curl -fsS -m 30 $(API)/ready && echo

.PHONY: metrics
metrics: ## Dump the application metrics
	@curl -fsS -m 5 $(API)/metrics | grep -E '^medibot_' | grep -v '^#'

.PHONY: ingest
ingest: ## Index the demo documents
	@curl -fsS -m 600 -X POST $(API)/ingest/demo \
		$${API_KEY:+-H "X-API-Key: $$API_KEY"} && echo

.PHONY: documents
documents: ## List the indexed documents
	@curl -fsS -m 30 $(API)/documents $${API_KEY:+-H "X-API-Key: $$API_KEY"} && echo

# --- Utilities --------------------------------------------------------------

.PHONY: wheels
wheels: ## Pre-download Linux/arm64 wheels for an offline build
	# PyTorch's CPU index takes priority: the PyPI torch wheel would drag in the
	# whole CUDA stack (~3.6 GB) with no use on a CPU target.
	cd backend && $(PYTHON) -m pip download \
		--only-binary=:all: \
		--index-url https://download.pytorch.org/whl/cpu \
		--extra-index-url https://pypi.org/simple \
		--platform manylinux_2_28_aarch64 --platform manylinux_2_17_aarch64 \
		--platform manylinux2014_aarch64 --platform linux_aarch64 \
		--python-version 311 --implementation cp --abi cp311 --abi abi3 --abi none \
		-d wheels -r requirements.txt

.PHONY: secret
secret: ## Generate a strong API key to paste into .env
	@$(PYTHON) -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))"

.PHONY: clean
clean: ## Remove build artefacts and caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.ruff_cache frontend/.next
