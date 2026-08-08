/**
 * ContractView — renders the full consequence contract in four-section format.
 * PHASE 0 SCAFFOLD — implement in Phase 7.
 *
 * Four sections (CLAUDE.md §17):
 *   1. What will happen?    → ContractSection + operation impact details
 *   2. What cannot be undone? → reversibility classification + PermanentGate (if PERMANENT)
 *   3. Has this happened before? → HistoricalCard × top 3
 *   4. What does the system flag? → SystemFlags (policy violations, warnings)
 *
 * Decision panel (CLAUDE.md §17 — Human Approval Rules):
 *   APPROVE: disabled until reason ≥ 10 chars; for PERMANENT ops, PermanentGate checkbox must be checked
 *   REJECT: disabled until reason ≥ 10 chars
 *   MODIFY WITH CONSTRAINTS: text input for constraints
 *   REQUEST PREREQUISITE: form for required action
 *
 * Polling: uses useApprovalPolling hook for SSE/polling integration.
 */
