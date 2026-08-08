import React, { useState, useEffect, useCallback } from 'react'
import { OutcomeBadge } from '../App.jsx'

const auditStyles = `
  .audit-table-wrap { overflow-x: auto; }

  .audit-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    min-width: 800px;
  }

  .audit-table th {
    text-align: left;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-tertiary);
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    background: var(--surface);
    position: sticky;
    top: 0;
  }

  .audit-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
    color: var(--text-primary);
  }

  .audit-table tr:last-child td { border-bottom: none; }
  .audit-table tr:hover td { background: var(--surface-2); }

  .audit-intent {
    max-width: 240px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
  }

  .audit-reason {
    max-width: 200px;
    font-size: 11px;
    color: var(--text-secondary);
    font-style: italic;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .delta-positive { color: var(--warning); font-weight: 600; }
  .delta-negative { color: var(--info); }
  .delta-zero { color: var(--text-tertiary); }
  .delta-large { color: var(--danger); font-weight: 700; }

  .audit-stats {
    display: flex;
    gap: 20px;
    padding: 12px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 16px;
    flex-wrap: wrap;
  }

  .audit-stat { display: flex; flex-direction: column; gap: 2px; }
  .audit-stat-value { font-size: 20px; font-weight: 700; font-family: var(--mono); color: var(--text-primary); }
  .audit-stat-label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.4px; }
`

function formatDelta(delta) {
  if (delta == null) return { text: '—', cls: 'delta-zero' }
  const pct = delta * 100
  const abs = Math.abs(pct)
  const text = `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
  if (abs > 50) return { text, cls: 'delta-large' }
  if (abs > 10 && pct > 0) return { text, cls: 'delta-positive' }
  if (abs > 10) return { text, cls: 'delta-negative' }
  return { text, cls: 'delta-zero' }
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

function formatNumber(n) {
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

  // Compute stats
  const total = records.length
  const approved = records.filter(r => r.outcome === 'APPROVED_EXECUTED').length
  const rejected = records.filter(r => ['REJECTED', 'AUTO_REJECTED'].includes(r.outcome)).length
  const highDelta = records.filter(r => r.blast_radius_delta != null && Math.abs(r.blast_radius_delta) > 0.2).length

  return (
    <>
      <style>{auditStyles}</style>
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div className="page-title">Audit Log</div>
            <div className="page-subtitle">Completed operations with outcomes and blast radius accuracy</div>
          </div>
          <button
            className="btn btn-modify btn-sm"
            onClick={fetchAudit}
            style={{ fontWeight: 500 }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>⚠ {error}</div>}

        {loading ? (
          <div className="loading-center"><div className="spinner" /> Loading audit records…</div>
        ) : records.length === 0 ? (
          <div className="card">
            <div className="empty-state">
              <div className="empty-state-icon">📋</div>
              <div className="empty-state-title">No audit records yet</div>
              <div className="empty-state-desc">Operations will appear here after they complete review.</div>
            </div>
          </div>
        ) : (
          <>
            <div className="audit-stats">
              <div className="audit-stat">
                <span className="audit-stat-value">{total}</span>
                <span className="audit-stat-label">Total operations</span>
              </div>
              <div className="audit-stat">
                <span className="audit-stat-value" style={{ color: 'var(--auto)' }}>{approved}</span>
                <span className="audit-stat-label">Approved &amp; executed</span>
              </div>
              <div className="audit-stat">
                <span className="audit-stat-value" style={{ color: 'var(--danger)' }}>{rejected}</span>
                <span className="audit-stat-label">Rejected</span>
              </div>
              {highDelta > 0 && (
                <div className="audit-stat">
                  <span className="audit-stat-value" style={{ color: 'var(--warning)' }}>{highDelta}</span>
                  <span className="audit-stat-label">High delta (&gt;20%)</span>
                </div>
              )}
            </div>

            <div className="card">
              <div className="audit-table-wrap">
                <table className="audit-table">
                  <thead>
                    <tr>
                      <th>Outcome</th>
                      <th>Operation</th>
                      <th>Table</th>
                      <th>Type</th>
                      <th>Est. Rows</th>
                      <th>Actual Rows</th>
                      <th>Δ Accuracy</th>
                      <th>Reason</th>
                      <th>Timestamp</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map(r => {
                      const { text: deltaText, cls: deltaCls } = formatDelta(r.blast_radius_delta)
                      return (
                        <tr key={r.operation_id}>
                          <td><OutcomeBadge outcome={r.outcome} /></td>
                          <td>
                            <div className="audit-intent" title={r.intent_summary || r.operation_id}>
                              {r.intent_summary || r.operation_id}
                            </div>
                            <div className="mono text-xs text-secondary">{r.operation_id}</div>
                          </td>
                          <td className="mono text-sm">{r.primary_table || '—'}</td>
                          <td className="mono text-sm">{r.operation_type || '—'}</td>
                          <td className="mono text-sm">{formatNumber(r.estimated_rows)}</td>
                          <td className="mono text-sm">{formatNumber(r.actual_rows)}</td>
                          <td className={`mono text-sm ${deltaCls}`}>{deltaText}</td>
                          <td>
                            <div className="audit-reason" title={r.decision_reason}>
                              {r.decision_reason || '—'}
                            </div>
                          </td>
                          <td className="text-xs text-secondary" style={{ whiteSpace: 'nowrap' }}>
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
