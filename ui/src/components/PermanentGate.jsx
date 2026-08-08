/**
 * PermanentGate — acknowledgement checkbox for PERMANENT operations.
 * PHASE 0 SCAFFOLD — implement in Phase 7.
 *
 * Behavior (CLAUDE.md §17):
 *   Renders: [ I HAVE READ AND UNDERSTOOD THAT THIS ACTION CANNOT BE AUTOMATICALLY REVERSED ]
 *   Checkbox must be checked before the Approve button activates.
 *   Only rendered when contract.reversibility.classification === "PERMANENT".
 *
 * This is a non-negotiable UI enforcement of CLAUDE.md §22 invariant 1.
 */
