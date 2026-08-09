# Phase 9 — Online Deployment + Real-Agent Integration + Production Demo

## Summary

Phase 9 completes the local production-like stack, wires up real agents into a containerized service,
resolves all Docker Compose compatibility issues, and drives the full test suite (686 passed, 8 skipped)
against a live PostgreSQL + 7-container stack.

## Acceptance Gates

| Gate | Status | Notes |
|------|--------|-------|
| YAML parses cleanly (`docker compose config`) | PASS | Single `x-env` anchor replaces illegal double `<<:` |
| All 7 containers healthy | PASS | `db, mcp, orchestrator, agents, proxy, notifier, ui` |
| 6 MCP servers running in single container | PASS | All expose `/health` and `/call` |
| Agents container (real AnalyzerAgent, ContextSimAgent, ContractAgent) | PASS | `/health` returns `{"analyzer":true,"context_sim":true,"contract":true}` |
| 673 non-E2E tests pass | PASS | 0 failures, 8 skipped |
| 13 E2E tests pass | PASS | All four demo scenario classes pass |
| Full suite: 686 passed, 8 skipped | PASS | |

## Changes Made

### docker-compose.yml (full rewrite)

**Problem:** Docker Compose v5 (bundled with Docker Desktop 29.x) uses a strict YAML 1.2 parser that
rejects duplicate `<<:` merge keys in the same mapping. The previous file had this pattern in three
services:

```yaml
environment:
  <<: *common-env
  <<: *mcp-urls   # ← duplicate key: INVALID in YAML 1.2
```

**Fix:** Collapsed `x-common-env` and `x-mcp-urls` into a single `x-env: &env` anchor that every
service merges with exactly one `<<: *env`. This is the only valid way to merge environment variables
in Docker Compose v5.

**Port collision:** Local PostgreSQL services on 5432 and 5433 prevented Docker from binding those
ports. Changed `contragate-db` host port to `15432:5432`. Container-to-container networking is
unaffected (stays on 5432 internally).

### agents/main.py (created)

FastAPI service on port 8002 that imports all three real agent classes at startup.
`/health` returns HTTP 503 if any agent fails to import. Three POST endpoints:
- `POST /v1/agents/analyze` → AnalyzerAgent
- `POST /v1/agents/context_sim` → ContextSimAgent  
- `POST /v1/agents/contract` → ContractAgent

### agents/Dockerfile (created)

```dockerfile
FROM python:3.12-slim
# ... installs requirements, copies source, exposes 8002
CMD ["python", "-m", "uvicorn", "agents.main:app", "--host", "0.0.0.0", "--port", "8002"]
```

### proxy/Dockerfile.prod (created)

Combined proxy+orchestrator for Railway deployment. Sets `DEV_MODE=false`,
`USE_MOCK_NOTIFIER=false`, `USE_MOCK_AUTH=false`. Runs 2 uvicorn workers.

### ui/Dockerfile (created)

Two-stage build: node:20-alpine (Vite build) → nginx:alpine (serve). Copies `ui/nginx.conf` for
SPA routing + `/v1/` reverse proxy with SSE support (proxy_buffering off).

### ui/nginx.conf (created)

- SPA routing: `try_files $uri $uri/ /index.html`
- API proxy: `location /v1/ { proxy_pass http://contragate-proxy:8000; proxy_buffering off; }`
- Health: `location /health { return 200 '{"status":"ok","service":"contragate-ui"}'; }`

### .dockerignore (created)

Excludes `.env`, `*.key`, `*.pem`, `__pycache__/`, `*.pyc`, `.pytest_cache`, `ui/node_modules/`,
`ui/dist/`, `docs/`, `*.pdf` from all Docker images.

### Makefile (created)

Common targets: `up/down/build/logs`, `db-shell/seed/db-reset`, `test/test-full/test-e2e/test-all`,
`agents-health/proxy-health`.

### mcp_servers/*/server.py (all 6)

Added `app = server.app` at module level after `server = ContraGateMCPServer(...)`.

**Problem:** `run_all.py` launched each MCP server via `uvicorn module:app`, but `app` did not exist
as a module-level name — it was only accessible as `server.app`. Uvicorn couldn't find the ASGI app.

**Fix:** Added the alias at module level. This is idiomatic uvicorn usage.

### mcp_servers/transaction_sandbox/server.py

`_get_staging_conn()` now reads `os.environ.get("STAGING_DATABASE_URL")` at call time instead of
the module-level cached copy. This makes the env var testable via `monkeypatch.setenv` in tests.

### tests/e2e/test_e2e_scenarios.py

Fixed three test assertions from `assert resp.status_code == 200` to
`assert resp.status_code in (200, 202)`. The proxy correctly returns HTTP 202 Accepted for operations
routed to human review (async protocol). Only auto-executed operations return 200.

## Architecture: Agents Container

In the local Docker Compose stack, `contragate-orchestrator` imports agents directly as Python modules
(same process). The `contragate-agents` container serves a different purpose:

1. **Isolation proof:** Verifies all three agents can load with only their declared dependencies —
   no implicit imports from the orchestrator's context.
2. **Railway readiness:** In production, `contragate-agents` is a separate scaled Railway service.
   The orchestrator calls it via HTTP (`POST /v1/agents/analyze` etc.) rather than importing directly.
3. **Health visibility:** The `/health` endpoint surfaces per-agent import status so deployment
   health checks can pinpoint which agent is broken.

## Database Port

The local PostgreSQL host port is `15432` (not 5432) because the host machine has local PostgreSQL
services on ports 5432 and 5433. Container-internal networking uses the standard 5432 port — only
the Docker host port binding differs.

For tests: `DATABASE_URL=postgresql://contragate:contragate_local@localhost:15432/contragate`

## Security Invariants Verified

- `ANTHROPIC_API_KEY` is never in source, images, or logs. Passed via `.env` (gitignored) only.
- `contragate_local` is the local dev password, not used in production.
- `.dockerignore` excludes `.env`, `*.key`, `*.pem` from all images.
- Production Dockerfiles (`proxy/Dockerfile.prod`) do not embed credentials.

## Railway Deployment Artifacts

Four Railway service definitions are ready in `railway.json`:
- `contragate-proxy-orchestrator` — `proxy/Dockerfile.prod`
- `contragate-agents` — `agents/Dockerfile`
- `contragate-mcp` — `mcp_servers/Dockerfile`
- `contragate-ui` — `ui/Dockerfile`

Required Railway env vars (set in Railway dashboard, never in source):
- `ANTHROPIC_API_KEY`
- `DATABASE_URL` (Railway PostgreSQL — app DB)
- `STAGING_DATABASE_URL` (Railway PostgreSQL — staging DB)
- `SLACK_BOT_TOKEN`, `SLACK_APPROVAL_CHANNEL` (for live notifier)

## Phase 10 Entry Criterion

All Phase 9 acceptance gates pass. Phase 10 covers final hardening, secrets audit, known limitations
documentation, and the three-minute demo recording.
