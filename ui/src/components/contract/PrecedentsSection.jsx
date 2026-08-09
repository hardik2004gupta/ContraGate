import React from 'react'
import { OutcomeBadge } from '../shared/Badge.jsx'

function formatDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric'
    })
  } catch (_) { return iso }
}

const OUTCOME_SEVERITY = {
  REJECTED:     { color: 'var(--red)',    icon: '✗' },
  ROLLED_BACK:  { color: 'var(--amber)',  icon: '↺' },
  MODIFIED:     { color: 'var(--blue)',   icon: '✎' },
  APPROVED:     { color: 'var(--green)',  icon: '✓' },
  APPROVED_EXECUTED: { color: 'var(--green)', icon: '✓' },
  AUTO_EXECUTED: { color: 'var(--cyan)', icon: '⚡' },
}

export default function PrecedentsSection({ precedents = [], retrievalAvailable = true }) {
  if (!retrievalAvailable) {
    return (
      <div className="warn-banner">
        📭 Memory store unavailable — historical context not loaded for this operation.
      </div>
    )
  }

  if (precedents.length === 0) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '32px 16px',
        gap: '8px',
        color: 'var(--text-3)',
        textAlign: 'center',
      }}>
        <span style={{ fontSize: '24px', opacity: 0.5 }}>📭</span>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-2)' }}>
          No similar operations found in memory
        </div>
        <div style={{ fontSize: '12px' }}>
          This appears to be a novel operation — no historical precedents were retrieved.
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        marginBottom: '4px',
      }}>
        <div style={{
          fontSize: '10px',
          fontWeight: '700',
          letterSpacing: '0.8px',
          textTransform: 'uppercase',
          color: 'var(--text-3)',
        }}>Institutional Memory</div>
        <div className="stat-chip" style={{ padding: '3px 8px', fontSize: '11px' }}>
          <span className="stat-chip-value">{precedents.length}</span>
          <span style={{ color: 'var(--text-3)' }}>similar operations</span>
        </div>
      </div>

      {precedents.map((h, i) => {
        const isTop = i === 0
        const severity = OUTCOME_SEVERITY[h.outcome] || { color: 'var(--text-2)', icon: '?' }
        const isNegative = ['REJECTED', 'ROLLED_BACK'].includes(h.outcome)

        return (
          <div key={h.operation_id || i} style={{
            position: 'relative',
            background: isTop
              ? (isNegative ? 'var(--red-dim)' : 'var(--surface-3)')
              : 'var(--surface-1)',
            border: `1px solid ${isTop ? (isNegative ? 'var(--red-border)' : 'var(--border-2)') : 'var(--border-1)'}`,
            borderRadius: 'var(--r-md)',
            padding: '14px 16px',
            animation: 'fadeIn 200ms ease-out both',
            animationDelay: `${i * 60}ms`,
          }}>
            {/* Top badge for closest match */}
            {isTop && (
              <div style={{
                position: 'absolute',
                top: '-1px',
                right: '12px',
                fontSize: '9px',
                fontWeight: '800',
                background: isNegative ? 'var(--red)' : 'var(--violet)',
                color: '#fff',
                padding: '2px 8px',
                borderRadius: '0 0 6px 6px',
                letterSpacing: '0.6px',
                textTransform: 'uppercase',
              }}>
                {isNegative ? '⚠ Closest Match — Rejected' : 'Closest Match'}
              </div>
            )}

            {/* Header row */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              flexWrap: 'wrap',
              marginBottom: '8px',
            }}>
              <OutcomeBadge outcome={h.outcome} />
              {h.operation_id && (
                <span className="mono-tag">{h.operation_id}</span>
              )}
              {h.created_at && (
                <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>{formatDate(h.created_at)}</span>
              )}
            </div>

            {/* Intent */}
            <div style={{
              fontSize: '13px',
              color: isNegative ? 'var(--text-0)' : 'var(--text-1)',
              lineHeight: '1.5',
              marginBottom: '8px',
              fontWeight: isTop ? '500' : '400',
            }}>
              {h.intent_summary || h.raw_sql}
            </div>

            {/* Tables */}
            {h.tables?.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '8px' }}>
                {h.tables.map(t => (
                  <span key={t} className="mono-tag">{t}</span>
                ))}
              </div>
            )}

            {/* Decision reason */}
            {h.decision_reason && (
              <div style={{
                fontSize: '12px',
                color: isNegative ? 'var(--red-light)' : 'var(--text-2)',
                borderLeft: `3px solid ${isNegative ? 'var(--red)' : 'var(--border-3)'}`,
                paddingLeft: '10px',
                lineHeight: '1.6',
                fontStyle: 'italic',
              }}>
                "{h.decision_reason}"
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
