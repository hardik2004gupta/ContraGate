import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { RiskTierBadge } from '../App.jsx'

const queueStyles = `
  .queue-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px;
  }

  .queue-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    cursor: pointer;
    transition: box-shadow 0.15s, border-color 0.15s;
  }

  .queue-card:hover {
    box-shadow: var(--shadow-md);
    border-color: var(--border-strong);
  }

  .queue-card.tier-full { border-left: 4px solid var(--full); }
  .queue-card.tier-standard { border-left: 4px solid var(--standard); }
  .queue-card.tier-auto { border-left: 4px solid var(--auto); }

  .card-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
  }

  .card-badges { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

  .card-intent {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-primary);
    line-height: 1.4;
  }

  .card-meta-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }

  .meta-item { display: flex; flex-direction: column; gap: 1px; }
  .meta-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--text-tertiary);
  }

  .meta-value {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    font-family: var(--mono);
  }

  .card-action { display: flex; justify-content: flex-end; }

  .review-link {
    background: var(--text-primary);
    color: #fff;
    padding: 7px 14px;
    border-radius: var(--radius-sm);
    font-size: 12px;
    font-weight: 600;
    text-decoration: none;
    transition: opacity 0.1s;
    cursor: pointer;
    border: none;
  }

  .review-link:hover { opacity: 0.85; }

  .queue-stats {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 10px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 20px;
    font-size: 13px;
  }

  .stat-chip { display: flex; align-items: center; gap: 6px; }
  .stat-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
`

function formatCountdown(secondsRemaining) {
  if (secondsRemaining <= 0) return { text: 'Timed out', cls: 'countdown-urgent' }
  const m = Math.floor(secondsRemaining / 60)
  const s = secondsRemaining % 60
  const text = m > 0 ? `${m}m ${s}s` : `${s}s`
  const cls = secondsRemaining < 120 ? 'countdown-urgent' : secondsRemaining < 300 ? 'countdown-warn' : 'countdown-ok'
  return { text, cls }
}

function tierClass(tier) {
  if (tier === 'FULL_CONTRACT') return 'tier-full'
  if (tier === 'STANDARD_REVIEW') return 'tier-standard'
  return 'tier-auto'
}

function formatNumber(n) {
  if (n == null || n === 0) return '—'
  return Number(n).toLocaleString()
}

export default function ApprovalQueue() {
  const [approvals, setApprovals] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  const fetchApprovals = useCallback(async () => {
    try {
      const res = await fetch('/v1/approvals')
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      const data = await res.json()
      setApprovals(data.approvals || [])
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchApprovals()
    const interval = setInterval(fetchApprovals, 4000)
    return () => clearInterval(interval)
  }, [fetchApprovals])

  const sorted = [...approvals].sort((a, b) => a.seconds_remaining - b.seconds_remaining)
  const urgentCount = sorted.filter(a => a.seconds_remaining < 300).length
  const fullCount = sorted.filter(a => a.risk_tier === 'FULL_CONTRACT').length

  return (
    <>
      <style>{queueStyles}</style>
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div className="page-title">Review Queue</div>
            <div className="page-subtitle">Pending consequence contracts awaiting human approval</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="spinner" style={{ borderTopColor: '#6B7280', borderColor: '#E5E7EB' }} />
            <span className="text-sm text-secondary">Live</span>
          </div>
        </div>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>⚠ {error}</div>}

        {loading ? (
          <div className="loading-center"><div className="spinner" /> Loading pending approvals…</div>
        ) : sorted.length === 0 ? (
          <div className="card">
            <div className="empty-state">
              <div className="empty-state-icon">✅</div>
              <div className="empty-state-title">No pending approvals</div>
              <div className="empty-state-desc">All operations have been reviewed or are on the auto-execute path.</div>
            </div>
          </div>
        ) : (
          <>
            <div className="queue-stats">
              <div className="stat-chip">
                <div className="stat-dot" style={{ background: '#9CA3AF' }} />
                <span><strong>{sorted.length}</strong> pending</span>
              </div>
              {urgentCount > 0 && (
                <div className="stat-chip">
                  <div className="stat-dot" style={{ background: 'var(--danger)' }} />
                  <span><strong>{urgentCount}</strong> urgent (&lt;5 min)</span>
                </div>
              )}
              {fullCount > 0 && (
                <div className="stat-chip">
                  <div className="stat-dot" style={{ background: 'var(--full)' }} />
                  <span><strong>{fullCount}</strong> full contract</span>
                </div>
              )}
            </div>

            <div className="queue-grid">
              {sorted.map(item => {
                const cd = formatCountdown(item.seconds_remaining)
                return (
                  <div
                    key={item.approval_id}
                    className={`queue-card ${tierClass(item.risk_tier)}`}
                    onClick={() => navigate(`/approval/${item.approval_id}`)}
                  >
                    <div className="card-top">
                      <div className="card-badges">
                        <RiskTierBadge tier={item.risk_tier} />
                        {item.prompt_injection_risk && (
                          <span className="badge badge-rejected">🚨 Injection Risk</span>
                        )}
                        {item.historical_rejection_count > 0 && (
                          <span className="badge badge-rolled-back">
                            ⚠ {item.historical_rejection_count} rejection{item.historical_rejection_count > 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                      <span className={`countdown ${cd.cls}`}>{cd.text}</span>
                    </div>

                    <div className="card-intent">
                      {item.intent_summary || `${item.operation_type} on ${item.primary_table}`}
                    </div>

                    <div className="card-meta-row">
                      <div className="meta-item">
                        <span className="meta-label">Table</span>
                        <span className="meta-value">{item.primary_table || '—'}</span>
                      </div>
                      <div className="meta-item">
                        <span className="meta-label">Est. Rows</span>
                        <span className="meta-value">{formatNumber(item.estimated_rows)}</span>
                      </div>
                      <div className="meta-item">
                        <span className="meta-label">Reversibility</span>
                        <span
                          className="meta-value"
                          style={{
                            fontSize: 11,
                            color: item.reversibility === 'PERMANENT' ? 'var(--permanent)' : 'inherit'
                          }}
                        >
                          {item.reversibility || '—'}
                        </span>
                      </div>
                    </div>

                    <div className="card-action">
                      <button className="review-link">Review Contract →</button>
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </>
  )
}
