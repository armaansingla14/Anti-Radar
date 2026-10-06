# ANTI RADAR — developer commands
.PHONY: up down dev api web db migrate seed demo capture ingest score brief test test-unit lint screenshots

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

test:          ## Backend unit + integration tests (needs `make db`)
	cd backend && uv run pytest -q

test-unit:
	cd backend && uv run pytest -q tests/unit

lint:
	cd backend && uv run ruff check radar tests
	cd frontend && npm run lint

screenshots:   ## Playwright screenshots for the README (stack must be running)
	cd e2e && npx playwright test
