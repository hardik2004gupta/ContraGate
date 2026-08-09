import React, { useState, useEffect, useCallback } from 'react'
import { OutcomeBadge } from '../components/shared/Badge.jsx'

function formatDelta(delta) {
  if (delta == null) return { text: '—', color: 'var(--text-3)' }
  const pct = delta * 100
  const abs = Math.abs(pct)
  const text = `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
  if (abs > 50) return { text, color: 'var(--red)', weight: '700' }
  if (abs > 10 && pct > 0) return { text, color: 'var(--amber)', weight: '600' }
  if (abs > 10) return { text, color: 'var(--blue)', weight: '600' }
  return { text, color: 'var(--text-3)' }
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch (_) { return iso }
}

function fmtNum(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

export default function AuditLog() {
  const [records, setRecords] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchAudit = useCallback(async () => {
    try {
      const res = await fetch('/v1/audit')
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      const data = await res.json()
      setRecords(data.records || [])
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAudit()
    const t = setInterval(fetchAudit, 8000)
    return () => clearInterval(t)
  }, [fetchAudit])

  const total = records.length
  const approved = records.filter(r => r.outcome === 'APPROVED_EXECUTED').length
  const rejected = records.filter(r => ['REJECTED', 'AUTO_REJECTED'].includes(r.outcome)).length
  const highDelta = records.filter(r => r.blast_radius_delta != null && Math.abs(r.blast_radius_delta) > 0.2).length

  return (
    <>
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div className="page-title">Audit Log</div>
            <div className="page-subtitle">Completed operations with outcomes and blast radius accuracy</div>
          </div>
          <button
            onClick={fetchAudit}
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--border-1)',
              borderRadius: 'var(--r-sm)',
              color: 'var(--text-2)',
              fontSize: '12px',
              fontWeight: '600',
              padding: '7px 14px',
              cursor: 'pointer',
              fontFamily: 'var(--font)',
              transition: 'background var(--t-1)',
            }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: '16px' }}>⚠ {error}</div>}

        {loading ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            gap: '10px', padding: '48px', color: 'var(--text-3)', fontSize: '13px',
          }}>
            <span className="spinner" /> Loading audit records…
          </div>
        ) : records.length === 0 ? (
          <div className="empty-state">
            <div style={{ fontSize: '28px', opacity: 0.4 }}>📋</div>
            <div className="empty-state-title">No audit records yet</div>
            <div className="empty-state-desc">Operations will appear here after they complete review.</div>
          </div>
        ) : (
          <>
            {/* Stats row */}
            <div className="stat-strip" style={{ marginBottom: '20px' }}>
              <div className="stat-chip">
                <span className="stat-chip-value">{total}</span>
                <span style={{ color: 'var(--text-3)' }}>total</span>
              </div>
              <div className="stat-chip">
                <span className="stat-chip-value" style={{ color: 'var(--green)' }}>{approved}</span>
                <span style={{ color: 'var(--text-3)' }}>approved</span>
              </div>
              <div className="stat-chip">
                <span className="stat-chip-value" style={{ color: 'var(--red)' }}>{rejected}</span>
                <span style={{ color: 'var(--text-3)' }}>rejected</span>
              </div>
              {highDelta > 0 && (
                <div className="stat-chip">
                  <span className="stat-chip-value" style={{ color: 'var(--amber)' }}>{highDelta}</span>
                  <span style={{ color: 'var(--text-3)' }}>high Δ (&gt;20%)</span>
                </div>
              )}
            </div>

            <div style={{
              background: 'var(--surface-1)',
              border: '1px solid var(--border-1)',
              borderRadius: 'var(--r-lg)',
              overflow: 'hidden',
            }}>
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ minWidth: '820px' }}>
                  <thead>
                    <tr>
                      <th>Outcome</th>
                      <th>Operation</th>
                      <th>Table</th>
                      <th>Type</th>
                      <th>Est. Rows</th>
                      <th>Actual</th>
                      <th>Δ Accuracy</th>
                      <th>Reason</th>
                      <th>Timestamp</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map(r => {
                      const { text: deltaText, color: deltaColor, weight: deltaWeight } = formatDelta(r.blast_radius_delta)
                      return (
                        <tr key={r.operation_id}>
                          <td><OutcomeBadge outcome={r.outcome} /></td>
                          <td>
                            <div style={{
                              maxWidth: '200px',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                              fontSize: '12px',
                              color: 'var(--text-1)',
                              marginBottom: '2px',
                            }} title={r.intent_summary || r.operation_id}>
                              {r.intent_summary || r.operation_id}
                            </div>
                            <div style={{ fontSize: '10px', fontFamily: 'var(--mono)', color: 'var(--text-3)' }}>
                              {r.operation_id}
                            </div>
                          </td>
                          <td>
                            <span className="mono-tag">{r.primary_table || '—'}</span>
                          </td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: '12px', color: 'var(--text-2)' }}>
                            {r.operation_type || '—'}
                          </td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: '12px', color: 'var(--text-1)' }}>
                            {fmtNum(r.estimated_rows)}
                          </td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: '12px', color: 'var(--cyan)' }}>
                            {fmtNum(r.actual_rows)}
                          </td>
                          <td style={{
                            fontFamily: 'var(--mono)',
                            fontSize: '12px',
                            color: deltaColor,
                            fontWeight: deltaWeight || '400',
                          }}>
                            {deltaText}
                          </td>
                          <td>
                            <div style={{
                              maxWidth: '180px',
                              fontSize: '11px',
                              color: 'var(--text-2)',
                              fontStyle: 'italic',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }} title={r.decision_reason}>
                              {r.decision_reason || '—'}
                            </div>
                          </td>
                          <td style={{ fontSize: '11px', color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                            {formatDate(r.updated_at)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}
