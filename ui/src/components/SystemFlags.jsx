/**
 * SystemFlags — policy violations and risk tags (Section 4 of contract).
 * PHASE 0 SCAFFOLD — implement in Phase 7.
 *
 * Displays (CLAUDE.md §17 — Section 4):
 *   - Policy violations (from contract.policy_violations)
 *   - Historical rejection warnings ("Two prior operations with identical table pattern rejected")
 *   - Simulation warnings (if simulation_available === false)
 *   - Memory retrieval warnings (if retrieval_available === false)
 *   - Blast radius confidence with reason
 *   - Prompt injection risk tag (if prompt_injection_risk === true)
 */
