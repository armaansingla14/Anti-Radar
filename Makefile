# ANTI RADAR — developer commands
.PHONY: up down dev api web db migrate seed demo capture ingest score brief test test-unit lint screenshots check-config ready docker-backend

up:            ## Full stack in Docker (db, api, worker, web) → http://localhost:3000
	docker compose up --build -d db api worker web
	@echo "ANTI RADAR → http://localhost:3000   API → http://localhost:8000/docs"

down:
	docker compose down

db:            ## Postgres only (for local dev)
	docker compose up -d db

migrate:
	cd backend && uv run alembic upgrade head

seed: migrate  ## Load the committed real-data snapshot + labeled fictional demo set, then score
	cd backend && uv run radar seed-demo

api:           ## Backend with reload on :8000
	cd backend && uv run uvicorn radar.api.main:app --reload --port 8000

web:           ## Frontend dev server on :3000
	cd frontend && npm run dev

dev: db migrate ## Run api + web locally (two processes)
	$(MAKE) -j2 api web

capture:       ## Re-capture the real-data demo snapshot from live public APIs
	cd backend && uv run radar demo capture

ingest:        ## Live sweep of all sources
	cd backend && uv run radar ingest all

score:
	cd backend && uv run radar score

brief:
	cd backend && uv run radar brief

check-config:  ## Validate the current environment's configuration (as the API would at startup)
	cd backend && uv run python -m radar.config_check

ready:         ## Liveness + readiness of a running API (API_URL, default http://localhost:8000)
	curl -fsS $${API_URL:-http://localhost:8000}/api/health && echo && curl -fsS $${API_URL:-http://localhost:8000}/api/ready && echo

docker-backend: ## Build the deployable backend image (repo-root context, same as Render)
	docker build -f backend/Dockerfile -t anti-radar-backend .

test:          ## Backend unit + integration tests (needs `make db`)
	cd backend && uv run pytest -q

test-unit:
	cd backend && uv run pytest -q tests/unit

lint:
	cd backend && uv run ruff check radar tests
	cd frontend && npx tsc --noEmit && npm run lint

screenshots:   ## Playwright screenshots for the README (stack must be running)
	cd e2e && npx playwright test
