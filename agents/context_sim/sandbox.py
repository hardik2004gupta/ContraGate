"""
Transaction sandbox execution logic.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 4.

Transaction sandbox sequence (CLAUDE.md §18):
1. begin_sandbox_transaction      → Open explicit transaction on staging instance
2. set_sandbox_mode               → SET LOCAL app.sandbox_mode = 'true'
                                    SET LOCAL statement_timeout = '5000ms'
3. Capture pre-execution counts   → SELECT COUNT(*) on each affected table
4. execute_in_transaction         → Run proposed SQL inside transaction
5. Capture post-execution counts  → SELECT COUNT(*) on each affected table
6. read_trigger_log               → Read sandbox_trigger_log for would-have-fired calls
7. rollback_transaction           → ROLLBACK — staging data never changes permanently

All sandbox operations go through the transaction-sandbox MCP server.
NEVER connect directly to any database from this module.

CRITICAL INVARIANTS (CLAUDE.md §22):
- Invariant 4: No production writes from simulation
- Invariant 5: No sandbox external network egress
- Staging instance must be writable (not a physical read replica)

Retry policy:
- On timeout (statement_timeout exceeded): retry with exponential backoff
- Maximum 2 retry attempts before marking simulation_available = False
"""
