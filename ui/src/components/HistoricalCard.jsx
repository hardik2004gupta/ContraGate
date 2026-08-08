/**
 * HistoricalCard — displays a single historical precedent from memory retrieval.
 * PHASE 0 SCAFFOLD — implement in Phase 7.
 *
 * Shows (CLAUDE.md §17 — Section 3):
 *   - Outcome badge (REJECTED, ROLLED_BACK, APPROVED, MODIFIED)
 *   - Operation ID and date
 *   - Intent summary
 *   - Affected tables
 *   - Stated reason (verbatim from the historical decision)
 *
 * Top result (highest reranking score) is displayed most prominently.
 * Ranked by outcome severity: REJECTED > ROLLED_BACK > blast_radius_delta > MODIFIED > APPROVED.
 */
