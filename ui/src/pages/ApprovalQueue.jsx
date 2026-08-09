import React, { useState, useEffect, useCallback } from 'react'
import ApprovalCard from '../components/approval/ApprovalCard.jsx'
import { QueueSkeleton } from '../components/shared/Skeleton.jsx'

export default function ApprovalQueue() {
  const [approvals, setApprovals] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

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
  const standardCount = sorted.filter(a => a.risk_tier === 'STANDARD_REVIEW').length

  return (
    <>
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div className="page-title">Review Queue</div>
            <div className="page-subtitle">Pending consequence contracts awaiting human approval</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="live-dot" />
            <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>Live</span>
          </div>
        </div>
      </div>

      <div className="page-body">
        {error && <div className="error-banner" style={{ marginBottom: '16px' }}>⚠ {error}</div>}

        {loading ? (
          <QueueSkeleton />
        ) : sorted.length === 0 ? (
          <div className="empty-state">
            <div style={{ fontSize: '32px', opacity: 0.5 }}>✓</div>
            <div className="empty-state-title">Queue clear</div>
            <div className="empty-state-desc">No operations pending review. The gate is open.</div>
          </div>
        ) : (
          <>
            {/* Stats strip */}
            <div className="stat-strip" style={{ marginBottom: '16px' }}>
              <div className="stat-chip">
                <span style={{
                  width: '7px', height: '7px', borderRadius: '50%',
                  background: 'var(--text-3)', display: 'inline-block'
                }} />
                <span className="stat-chip-value">{sorted.length}</span>
                <span style={{ color: 'var(--text-3)' }}>pending</span>
              </div>
              {urgentCount > 0 && (
                <div className="stat-chip">
                  <span style={{
                    width: '7px', height: '7px', borderRadius: '50%',
                    background: 'var(--red)', display: 'inline-block',
                    boxShadow: '0 0 4px var(--red)',
                  }} />
                  <span className="stat-chip-value" style={{ color: 'var(--red)' }}>{urgentCount}</span>
                  <span style={{ color: 'var(--text-3)' }}>urgent</span>
                </div>
              )}
              {fullCount > 0 && (
                <div className="stat-chip">
                  <span style={{
                    width: '7px', height: '7px', borderRadius: '50%',
                    background: 'var(--red)', display: 'inline-block',
                  }} />
                  <span className="stat-chip-value">{fullCount}</span>
                  <span style={{ color: 'var(--text-3)' }}>full contract</span>
                </div>
              )}
              {standardCount > 0 && (
                <div className="stat-chip">
                  <span style={{
                    width: '7px', height: '7px', borderRadius: '50%',
                    background: 'var(--amber)', display: 'inline-block',
                  }} />
                  <span className="stat-chip-value">{standardCount}</span>
                  <span style={{ color: 'var(--text-3)' }}>standard</span>
                </div>
              )}
            </div>

            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
              gap: '16px',
            }}>
              {sorted.map(item => (
                <ApprovalCard key={item.approval_id} item={item} />
              ))}
            </div>
          </>
        )}
      </div>
    </>
  )
}
