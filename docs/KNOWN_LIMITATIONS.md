# ContraGate — Known Limitations

This document records limitations that exist in the current implementation. It is honest.
It separates what is an intentional MVP boundary from what is a future gap.

---

## MVP Scope Limitations (By Design)

These are not defects. The architecture explicitly bounds the MVP to these constraints.

### 1. PostgreSQL Only

ContraGate intercepts SQL-modifying MCP tool calls targeting PostgreSQL only. The architecture
is designed for extension to other tool domains (MySQL, MongoDB, file system, API calls) without
redesign, but only PostgreSQL is implemented. Non-SQL MCP tools pass through without interception.

### 2. Single Tenant

The data schema supports `tenant_id` on every record. The MVP deploys and seeds `demo_tenant`
only. Multi-tenancy requires tenant provisioning infrastructure not built in the MVP.

### 3. Single Approver

The policy schema includes a `dual_approval_required` field. The MVP implements single-approver
workflows only. Dual-approval requires an additional approval state and notifier logic.

### 4. API Key Authentication Only

There is no OIDC, OAuth, or role-based authorization. Approvers are identified by API key
(`CONTRAGATE_MCP_API_KEY`). `USE_MOCK_AUTH=true` bypasses this in local development.

### 5. No Cross-Domain Consequence Propagation

The Propagation Agent (for tracing consequences across CRM, billing pipelines, etc.) is not
implemented. Webhook consequences are traced to the boundary (webhook URL detected) but external
downstream effects are not modeled.

### 6. Physical Read Replica Exclusion (By Architecture)

The transaction sandbox requires a writable staging instance. A physical PostgreSQL read replica
rejects writes even inside an intended `ROLLBACK` transaction, which breaks the simulation.
See ADR-002.

### 7. Non-Transactional Extension Simulation

`pg_net`, `dblink`, and other non-transactional extensions are detected and flagged but not
simulated. The sandbox records "would have fired" entries in `sandbox_trigger_log` but the actual
external call is not made (by design — the sandbox has no network egress). This is an honest
limitation: the system shows *that* an external call would fire and its parameters, but not its
actual downstream result.

---

## Production Limitations

### 8. Railway Deployment Not Verified in This Repository Version

All Railway deployment artifacts are complete and correct:
- `railway.json` — four service topology
- `proxy/Dockerfile.prod` — combined proxy+orchestrator
- `agents/Dockerfile` — real three-agent service
- `mcp_servers/Dockerfile` — all six MCP servers
- `ui/Dockerfile` — Vite + nginx

However, the railway.json deployment has not been executed against a live Railway account in this
repository version. The deployment requires:
- A Railway account with project provisioned
- Two PostgreSQL plugins (app DB + staging DB)
- `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` set in Railway environment

Local Docker Compose gives identical behavior to the Railway topology.

### 9. Slack Integration Requires SLACK_SIGNING_SECRET

The Slack webhook handler validates `X-Slack-Signature` using `SLACK_SIGNING_SECRET`. Without
this, production signature validation returns `False` and the endpoint rejects all Slack requests.
Set `SLACK_SIGNING_SECRET` in Railway environment variables — never in source.

### 10. pgvector Extension Required

The memory store depends on the `pgvector` extension for semantic similarity search. The local
Docker image (`pgvector/pgvector:pg16`) includes it. Railway PostgreSQL does not include pgvector
by default; you must either use a pgvector-enabled PostgreSQL provider or run the install script:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 11. SSE Queues Are In-Memory Only

Server-Sent Event subscriber queues are not persisted to PostgreSQL. After a process restart,
active SSE clients must reconnect. The approval contract itself survives restart (PostgreSQL
`workflow_records`), but SSE pushes resume only after client reconnects. The polling endpoint
(`GET /v1/approvals/{id}/status`) is unaffected by restart.

### 12. Embeddings API Calls in Memory Retrieval

Stage 1 retrieval (semantic search) embeds the current operation's intent using the Anthropic
Embeddings API. This adds latency (~200–500ms) and counts against API usage. Embeddings are
cached by intent text hash to avoid redundant calls for identical descriptions, but first-time
queries for each unique intent always make an API call.

---

## Future Roadmap (Not Implemented)

These are explicitly out of scope for the MVP and are listed only to prevent confusion:

- Multi-tenant provisioning and tenant isolation API
- OIDC / OAuth approver authentication
- Dual-approval workflows
- Propagation Agent for cross-system consequence tracing
- MySQL, MongoDB, and other database adapters
- Physical read replica sandbox (architecture decision: staging instance required)
- Physical execution of `pg_net` / `dblink` in sandbox
- Automatic rollback execution (LLM-generated rollback SQL is explicitly prohibited by architecture)
- Grafana / Prometheus observability integration
- Audit log export to S3 or external SIEM
- Mobile approval notifications (currently Slack only)
