# ContraGate Makefile — common local development operations
# Run `make help` to see all targets.

.PHONY: help up down build logs test test-unit test-integration test-e2e seed clean db-shell

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "ContraGate — Local Development Commands"
	@echo "======================================="
	@echo ""
	@echo "Stack:"
	@echo "  make up          Start all 7 containers (builds if needed)"
	@echo "  make down        Stop all containers"
	@echo "  make build       Build all container images"
	@echo "  make logs        Follow logs from all containers"
	@echo "  make logs-proxy  Follow proxy logs only"
	@echo ""
	@echo "Database:"
	@echo "  make db-shell    Open psql session to contragate-db"
	@echo "  make seed        (Re-)apply seed scripts to running DB"
	@echo "  make db-reset    Drop and recreate the database volume (fresh start)"
	@echo ""
	@echo "Testing:"
	@echo "  make test        Run unit + integration tests (no Docker needed)"
	@echo "  make test-full   Run all tests including PostgreSQL-dependent ones (Docker DB required)"
	@echo "  make test-e2e    Run E2E tests (full Docker stack required)"
	@echo ""
	@echo "Agents:"
	@echo "  make agents-health  Check agent service health"
	@echo ""

# ── Stack ─────────────────────────────────────────────────────────────────────

up:
	docker compose up -d
	@echo ""
	@echo "Waiting for all services to be healthy..."
	@docker compose ps

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

logs-proxy:
	docker compose logs -f contragate-proxy

logs-agents:
	docker compose logs -f contragate-agents

# ── Database ──────────────────────────────────────────────────────────────────

db-shell:
	docker compose exec contragate-db psql -U contragate -d contragate
	# Or from host: psql -h localhost -p 5433 -U contragate -d contragate

seed:
	@echo "Applying seed scripts..."
	docker compose exec -T contragate-db psql -U contragate -d contragate -f /docker-entrypoint-initdb.d/01_demo_schema.sql
	docker compose exec -T contragate-db psql -U contragate -d contragate -f /docker-entrypoint-initdb.d/02_staging_schema.sql
	docker compose exec -T contragate-db psql -U contragate -d contragate -f /docker-entrypoint-initdb.d/03_default_policies.sql
	docker compose exec -T contragate-db psql -U contragate -d contragate -f /docker-entrypoint-initdb.d/04_historical_operations.sql
	docker compose exec -T contragate-db psql -U contragate -d contragate -f /docker-entrypoint-initdb.d/05_sandbox_trigger.sql
	@echo "Seeds applied."

db-reset:
	docker compose down -v
	docker compose up contragate-db -d
	@echo "Database volume reset. Run 'make up' to restart the full stack."

# ── Testing ───────────────────────────────────────────────────────────────────

test:
	python -m pytest sql_analysis_lib/tests/ tests/ -m "not e2e" -q

test-full:
	@echo "Running full test suite (requires live PostgreSQL on port 5432)..."
	python -m pytest sql_analysis_lib/tests/ tests/ -m "not e2e" -v

test-e2e:
	@echo "Running E2E tests (requires full Docker stack on localhost:8000)..."
	python -m pytest tests/e2e/ -v -m e2e

test-all:
	python -m pytest sql_analysis_lib/tests/ tests/ -v

# ── Agents ────────────────────────────────────────────────────────────────────

agents-health:
	curl -s http://localhost:8002/health | python -m json.tool

proxy-health:
	curl -s http://localhost:8000/health | python -m json.tool
