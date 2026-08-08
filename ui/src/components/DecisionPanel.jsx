/**
 * DecisionPanel — decision options and reason capture.
 * PHASE 0 SCAFFOLD — implement in Phase 7.
 *
 * Decision options (CLAUDE.md §17):
 *   APPROVE           — Approve button disabled until reason.length >= 10
 *                       Also disabled if PermanentGate not acknowledged
 *   REJECT            — Reject button disabled until reason.length >= 10
 *   MODIFY WITH CONSTRAINTS — Opens text input for constraint specification
 *   REQUEST PREREQUISITE    — Opens form for required prerequisite action
 *
 * The reason field is required for APPROVE and REJECT.
 * Minimum 10 characters enforced at UI level.
 */
