import React, { useState } from 'react'
import PermanentGate from './PermanentGate.jsx'

const MIN_REASON = 10

const TERMINAL_META = {
  APPROVED:      { icon: '✓', label: 'Approved', color: 'var(--green)' },
  REJECTED:      { icon: '✕', label: 'Rejected', color: 'var(--red)' },
  TIMED_OUT:     { icon: '⏱', label: 'Timed Out — Auto-Rejected', color: 'var(--red)' },
  AUTO_REJECTED: { icon: '🚫', label: 'Auto-Rejected by Policy', color: 'var(--red)' },
  COMPLETED:     { icon: '✓', label: 'Completed', color: 'var(--green)' },
  AUTO_EXECUTED: { icon: '⚡', label: 'Auto-Executed', color: 'var(--cyan)' },
  FAILED:        { icon: '✕', label: 'Failed', color: 'var(--red)' },
}

function ExpandSection({ label, icon, open, onToggle, children }) {
  return (
    <div style={{
      border: `1px solid ${open ? 'var(--border-2)' : 'var(--border-1)'}`,
      borderRadius: 'var(--r-sm)',
      overflow: 'hidden',
      transition: 'border-color var(--t-2)',
    }}>
      <div
        role="button"
        onClick={onToggle}
        style={{
          padding: '10px 12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          cursor: 'pointer',
          background: open ? 'var(--surface-3)' : 'var(--surface-2)',
          transition: 'background var(--t-2)',
          userSelect: 'none',
        }}
      >
        <span style={{ fontSize: '12px', fontWeight: '600', color: 'var(--text-1)' }}>
          {icon} {label}
        </span>
        <span style={{
          fontSize: '10px',
          color: 'var(--text-3)',
          transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
          transition: 'transform var(--t-2)',
          display: 'inline-block',
        }}>▼</span>
      </div>
      {open && (
        <div style={{
          padding: '12px',
          borderTop: '1px solid var(--border-1)',
          animation: 'fadeIn 120ms ease-out',
        }}>
          {children}
        </div>
      )}
    </div>
  )
}

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
    const meta = TERMINAL_META[terminalStatus] || { icon: '📋', label: terminalStatus, color: 'var(--text-2)' }
    return (
      <div style={{
        background: 'var(--surface-1)',
        border: '1px solid var(--border-1)',
        borderRadius: 'var(--r-lg)',
        overflow: 'hidden',
      }}>
        <div style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--border-1)',
          fontSize: '10px',
          fontWeight: '800',
          letterSpacing: '1px',
          textTransform: 'uppercase',
          color: 'var(--text-3)',
        }}>Decision Recorded</div>
        <div style={{
          padding: '24px 16px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '8px',
          textAlign: 'center',
        }}>
          <div style={{
            width: '40px',
            height: '40px',
            borderRadius: '50%',
            background: meta.color + '22',
            border: `2px solid ${meta.color}44`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '18px',
            color: meta.color,
          }}>{meta.icon}</div>
          <div style={{ fontSize: '14px', fontWeight: '700', color: meta.color }}>{meta.label}</div>
        </div>
      </div>
    )
  }

  return (
    <div style={{
      background: 'var(--surface-1)',
      border: '1px solid var(--border-1)',
      borderRadius: 'var(--r-lg)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-1)',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
      }}>
        <div style={{
          width: '6px',
          height: '6px',
          borderRadius: '50%',
          background: 'var(--amber)',
          boxShadow: '0 0 6px var(--amber)',
          animation: 'pulse 2s ease-in-out infinite',
        }} />
        <span style={{
          fontSize: '10px',
          fontWeight: '800',
          letterSpacing: '1px',
          textTransform: 'uppercase',
          color: 'var(--text-2)',
        }}>Security Checkpoint</span>
      </div>

      <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
        {error && (
          <div className="error-banner">⚠ {error}</div>
        )}

        {/* Reason input */}
        <div>
          <div style={{
            fontSize: '10px',
            fontWeight: '700',
            letterSpacing: '0.8px',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
            marginBottom: '6px',
          }}>Decision Reason <span style={{ color: 'var(--red)', fontWeight: '400' }}>(required)</span></div>
          <textarea
            rows={3}
            placeholder="Explain your decision — minimum 10 characters."
            value={reason}
            onChange={e => setReason(e.target.value)}
            disabled={submitting}
            style={{ resize: 'vertical', minHeight: '72px' }}
          />
          <div style={{
            textAlign: 'right',
            fontSize: '11px',
            marginTop: '4px',
            color: reason.length >= MIN_REASON ? 'var(--green)' : 'var(--amber)',
            transition: 'color var(--t-1)',
          }}>
            {reason.length} / {MIN_REASON} min
          </div>
        </div>

        {/* PERMANENT gate — exact text invariant */}
        {isPermanent && (
          <PermanentGate checked={permanentAck} onChange={setPermanentAck} />
        )}

        {/* Primary actions */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
          <button
            className="btn-approve"
            disabled={!approveEnabled}
            onClick={() => sendDecision('APPROVE')}
            style={{
              padding: '12px',
              fontSize: '14px',
              fontWeight: '700',
              borderRadius: 'var(--r-md)',
              cursor: approveEnabled ? 'pointer' : 'not-allowed',
              opacity: approveEnabled ? 1 : 0.4,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              background: approveEnabled ? 'var(--green)' : 'var(--surface-3)',
              color: approveEnabled ? '#fff' : 'var(--text-3)',
              border: 'none',
              transition: 'all var(--t-2)',
            }}
          >
            {submitting ? <span className="spinner" /> : <span>✓</span>}
            Approve
          </button>
          <button
            className="btn-reject"
            disabled={!rejectEnabled}
            onClick={() => sendDecision('REJECT')}
            style={{
              padding: '12px',
              fontSize: '14px',
              fontWeight: '700',
              borderRadius: 'var(--r-md)',
              cursor: rejectEnabled ? 'pointer' : 'not-allowed',
              opacity: rejectEnabled ? 1 : 0.4,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              background: rejectEnabled ? 'var(--red-dim)' : 'var(--surface-3)',
              border: `1px solid ${rejectEnabled ? 'var(--red-border)' : 'var(--border-1)'}`,
              color: rejectEnabled ? 'var(--red)' : 'var(--text-3)',
              transition: 'all var(--t-2)',
            }}
          >
            {submitting ? <span className="spinner" /> : <span>✕</span>}
            Reject
          </button>
        </div>

        {/* Secondary actions */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <ExpandSection
            label="Modify With Constraints"
            icon="✎"
            open={modifyOpen}
            onToggle={() => { setModifyOpen(o => !o); setPrereqOpen(false) }}
          >
            <div style={{ fontSize: '10px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: '6px' }}>
              Constraints
            </div>
            <textarea
              rows={3}
              placeholder="e.g., Limit DELETE to 1,000 rows. Add WHERE created_at > 2024-01-01."
              value={modifyConstraints}
              onChange={e => setModifyConstraints(e.target.value)}
              disabled={submitting}
            />
            <button
              style={{
                marginTop: '8px',
                width: '100%',
                padding: '8px',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--violet-border)',
                background: 'var(--violet-dim)',
                color: 'var(--violet-light)',
                fontSize: '12px',
                fontWeight: '600',
                cursor: (!reasonOk || !modifyConstraints.trim() || submitting) ? 'not-allowed' : 'pointer',
                opacity: (!reasonOk || !modifyConstraints.trim() || submitting) ? 0.4 : 1,
                transition: 'opacity var(--t-1)',
              }}
              disabled={!reasonOk || !modifyConstraints.trim() || submitting}
              onClick={() => sendDecision('MODIFY', { modification_constraints: modifyConstraints.trim() })}
            >
              Send Modification
            </button>
          </ExpandSection>

          <ExpandSection
            label="Request Prerequisite"
            icon="◷"
            open={prereqOpen}
            onToggle={() => { setPrereqOpen(o => !o); setModifyOpen(false) }}
          >
            <div style={{ fontSize: '10px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: '6px' }}>
              Required Action
            </div>
            <textarea
              rows={3}
              placeholder="e.g., Run archival job before re-submitting this delete."
              value={prereqText}
              onChange={e => setPrereqText(e.target.value)}
              disabled={submitting}
            />
            <button
              style={{
                marginTop: '8px',
                width: '100%',
                padding: '8px',
                borderRadius: 'var(--r-sm)',
                border: '1px solid var(--blue-border)',
                background: 'var(--blue-dim)',
                color: 'var(--blue)',
                fontSize: '12px',
                fontWeight: '600',
                cursor: (!reasonOk || !prereqText.trim() || submitting) ? 'not-allowed' : 'pointer',
                opacity: (!reasonOk || !prereqText.trim() || submitting) ? 0.4 : 1,
                transition: 'opacity var(--t-1)',
              }}
              disabled={!reasonOk || !prereqText.trim() || submitting}
              onClick={() => sendDecision('REQUEST_PREREQUISITE', { prerequisite: prereqText.trim() })}
            >
              Request Prerequisite
            </button>
          </ExpandSection>
        </div>
      </div>
    </div>
  )
}
