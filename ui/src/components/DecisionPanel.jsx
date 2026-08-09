import React, { useState } from 'react'
import PermanentGate from './PermanentGate.jsx'

const panelStyles = `
  .decision-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
    display: flex;
    flex-direction: column;
    gap: 0;
    overflow: hidden;
  }

  .decision-panel-header {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: 0.1px;
    text-transform: uppercase;
  }

  .decision-panel-body {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .reason-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 4px;
  }

  .reason-hint {
    font-size: 11px;
    color: var(--text-tertiary);
    margin-top: 4px;
  }

  .decision-buttons {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .decision-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .decision-secondary {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .expand-section {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    overflow: hidden;
  }

  .expand-section-header {
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    background: var(--surface-2);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .expand-section-body {
    padding: 12px;
    border-top: 1px solid var(--border);
  }

  .decision-status-terminal {
    background: var(--surface-2);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    text-align: center;
  }

  .decision-status-terminal .status-emoji { font-size: 24px; margin-bottom: 8px; }
  .decision-status-terminal .status-label { font-size: 14px; font-weight: 700; }
  .decision-status-terminal .status-reason { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }

  .char-count {
    text-align: right;
    font-size: 11px;
    margin-top: 3px;
    color: var(--text-tertiary);
  }

  .char-count.ok { color: var(--auto); }
  .char-count.short { color: var(--warning); }
`

const MIN_REASON = 10

/**
 * DecisionPanel — APPROVE / REJECT / MODIFY / REQUEST_PREREQUISITE actions.
 *
 * Props:
 *   approvalId   — approval_id to post to
 *   isPermanent  — true when reversibility === "PERMANENT"
 *   isTerminal   — true when a decision has already been recorded
 *   terminalStatus — the terminal status string (for display)
 *   onDecisionSent — callback after successful POST
 */
export default function DecisionPanel({
  approvalId,
  isPermanent = false,
  isTerminal = false,
  terminalStatus = null,
  onDecisionSent,
}) {
  const [reason, setReason] = useState('')
  const [permanentAck, setPermanentAck] = useState(false)
  const [modifyOpen, setModifyOpen] = useState(false)
  const [prereqOpen, setPrereqOpen] = useState(false)
  const [modifyConstraints, setModifyConstraints] = useState('')
  const [prereqText, setPrereqText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const reasonOk = reason.trim().length >= MIN_REASON
  const approveEnabled = reasonOk && (!isPermanent || permanentAck) && !submitting
  const rejectEnabled = reasonOk && !submitting

  async function sendDecision(decision, extraBody = {}) {
    setSubmitting(true)
    setError(null)
    try {
      const body = { approval_id: approvalId, decision, reason: reason.trim(), ...extraBody }
      const res = await fetch('/v1/decisions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(detail.detail || 'Request failed')
      }
      onDecisionSent?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (isTerminal) {
    const statusMeta = {
      APPROVED: { emoji: '✅', label: 'Approved', cls: 'text-success' },
      REJECTED: { emoji: '❌', label: 'Rejected', cls: 'text-danger' },
      TIMED_OUT: { emoji: '⏱', label: 'Timed Out — Auto-Rejected', cls: 'text-danger' },
      AUTO_REJECTED: { emoji: '🚫', label: 'Auto-Rejected by Policy', cls: 'text-danger' },
      COMPLETED: { emoji: '✅', label: 'Completed', cls: 'text-success' },
      AUTO_EXECUTED: { emoji: '⚡', label: 'Auto-Executed', cls: 'text-success' },
    }
    const meta = statusMeta[terminalStatus] || { emoji: '📋', label: terminalStatus, cls: '' }
    return (
      <>
        <style>{panelStyles}</style>
        <div className="decision-panel">
          <div className="decision-panel-header">Decision Recorded</div>
          <div className="decision-panel-body">
            <div className="decision-status-terminal">
              <div className="status-emoji">{meta.emoji}</div>
              <div className={`status-label ${meta.cls}`}>{meta.label}</div>
            </div>
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <style>{panelStyles}</style>
      <div className="decision-panel">
        <div className="decision-panel-header">Your Decision</div>
        <div className="decision-panel-body">
          {error && (
            <div className="error-banner">⚠ {error}</div>
          )}

          {/* Reason input */}
          <div>
            <div className="reason-label">Reason (required)</div>
            <textarea
              rows={3}
              placeholder="Explain your decision — minimum 10 characters."
              value={reason}
              onChange={e => setReason(e.target.value)}
              disabled={submitting}
            />
            <div className={`char-count ${reason.length >= MIN_REASON ? 'ok' : 'short'}`}>
              {reason.length} / {MIN_REASON} min
            </div>
          </div>

          {/* PERMANENT gate */}
          {isPermanent && (
            <PermanentGate checked={permanentAck} onChange={setPermanentAck} />
          )}

          {/* Primary actions */}
          <div className="decision-buttons">
            <div className="decision-row">
              <button
                className="btn btn-approve btn-lg"
                disabled={!approveEnabled}
                onClick={() => sendDecision('APPROVE')}
              >
                {submitting ? <span className="spinner" /> : '✓'} Approve
              </button>
              <button
                className="btn btn-reject btn-lg"
                disabled={!rejectEnabled}
                onClick={() => sendDecision('REJECT')}
              >
                {submitting ? <span className="spinner" /> : '✕'} Reject
              </button>
            </div>

            {/* Secondary actions */}
            <div className="decision-secondary">
              <div className="expand-section">
                <div
                  className="expand-section-header"
                  onClick={() => { setModifyOpen(o => !o); setPrereqOpen(false) }}
                >
                  <span>Modify</span>
                  <span>{modifyOpen ? '▲' : '▼'}</span>
                </div>
                {modifyOpen && (
                  <div className="expand-section-body">
                    <div className="reason-label" style={{ marginBottom: 6 }}>Constraints</div>
                    <textarea
                      rows={3}
                      placeholder="e.g., Limit DELETE to 1,000 rows. Add WHERE created_at > 2024-01-01."
                      value={modifyConstraints}
                      onChange={e => setModifyConstraints(e.target.value)}
                      disabled={submitting}
                    />
                    <button
                      className="btn btn-modify btn-sm"
                      style={{ marginTop: 8, width: '100%' }}
                      disabled={!reasonOk || !modifyConstraints.trim() || submitting}
                      onClick={() => sendDecision('MODIFY', { modification_constraints: modifyConstraints.trim() })}
                    >
                      Send Modification
                    </button>
                  </div>
                )}
              </div>

              <div className="expand-section">
                <div
                  className="expand-section-header"
                  onClick={() => { setPrereqOpen(o => !o); setModifyOpen(false) }}
                >
                  <span>Prerequisite</span>
                  <span>{prereqOpen ? '▲' : '▼'}</span>
                </div>
                {prereqOpen && (
                  <div className="expand-section-body">
                    <div className="reason-label" style={{ marginBottom: 6 }}>Required action</div>
                    <textarea
                      rows={3}
                      placeholder="e.g., Run archival job before re-submitting this delete."
                      value={prereqText}
                      onChange={e => setPrereqText(e.target.value)}
                      disabled={submitting}
                    />
                    <button
                      className="btn btn-prereq btn-sm"
                      style={{ marginTop: 8, width: '100%' }}
                      disabled={!reasonOk || !prereqText.trim() || submitting}
                      onClick={() => sendDecision('REQUEST_PREREQUISITE', { prerequisite: prereqText.trim() })}
                    >
                      Request Prerequisite
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
