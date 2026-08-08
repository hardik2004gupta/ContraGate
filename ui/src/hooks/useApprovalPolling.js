/**
 * useApprovalPolling — SSE subscription with polling fallback.
 * PHASE 0 SCAFFOLD — implement in Phase 7.
 *
 * Subscribes to GET /v1/approvals/{id}/stream for real-time updates.
 * Falls back to polling GET /v1/approvals/{id}/status if SSE is unavailable.
 *
 * Returns: { status, result, error, isLoading }
 *
 * On APPROVED: returns result with actual execution output.
 * On REJECTED: returns reason.
 * On timeout: automatically reflects AUTO_REJECT status.
 *
 * See proxy/async_protocol.py for the server-side implementation.
 */
