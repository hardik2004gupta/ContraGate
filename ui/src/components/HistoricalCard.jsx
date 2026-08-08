import React from 'react'

const histStyles = `
  .historical-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .historical-card {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 12px 14px;
    background: var(--surface);
    position: relative;
  }

  .historical-card.rank-1 {
    border-color: #FECACA;
    background: #FFF9F9;
  }

  .historical-card-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }

  .historical-card-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .historical-op-id {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .historical-date {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .historical-card-intent {
    font-size: 13px;
    color: var(--text-primary);
    margin-bottom: 6px;
    line-height: 1.4;
  }

  .historical-tables {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: 6px;
  }

  .table-tag {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 11px;
    font-family: var(--mono);
    color: var(--text-secondary);
  }

  .historical-reason {
    font-size: 12px;
    color: var(--text-secondary);
    font-style: italic;
    border-left: 3px solid var(--border-strong);
    padding-left: 8px;
    margin-top: 4px;
    line-height: 1.5;
  }

  .historical-reason.reason-rejected {
    border-left-color: var(--rejected);
    color: #7F1D1D;
  }

  .historical-rank-label {
    position: absolute;
    top: -1px; right: 10px;
    font-size: 10px;
    font-weight: 700;
    background: var(--full);
    color: #fff;
    padding: 1px 6px;
    border-radius: 0 0 4px 4px;
    letter-spacing: 0.4px;
  }

  .no-history {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-tertiary);
    padding: 12px 0;
  }
`

function outcomeClass(outcome) {
  const map = {
    REJECTED: 'badge-rejected',
    ROLLED_BACK: 'badge-rolled-back',
    APPROVED: 'badge-approved',
    APPROVED_EXECUTED: 'badge-approved',
    MODIFIED: 'badge-modified',
  }
  return map[outcome] || 'badge-unknown'
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch (_) { return iso }
}

/**
 * HistoricalCard — renders up to 3 historical precedents from memory retrieval.
 * The top result (rank 1) is visually emphasized.
 */
export default function HistoricalCard({ precedents = [], retrievalAvailable = true }) {
  return (
    <>
      <style>{histStyles}</style>
      {!retrievalAvailable && (
        <div className="error-banner" style={{ marginBottom: 12 }}>
          ⚠ Memory store unavailable — historical context not loaded for this operation.
        </div>
      )}
      {precedents.length === 0 ? (
        <div className="no-history">
          <span>📭</span>
          <span>No similar operations found in memory.</span>
        </div>
      ) : (
        <div className="historical-list">
          {precedents.map((h, i) => {
            const isTop = i === 0
            const isRejected = h.outcome === 'REJECTED' || h.outcome === 'ROLLED_BACK'
            return (
              <div key={h.operation_id || i} className={`historical-card${isTop ? ' rank-1' : ''}`}>
                {isTop && <div className="historical-rank-label">CLOSEST MATCH</div>}
                <div className="historical-card-top">
                  <div className="historical-card-meta">
                    <span className={`badge ${outcomeClass(h.outcome)}`}>{h.outcome?.replace('_', ' ')}</span>
                    {h.operation_id && (
                      <span className="historical-op-id">{h.operation_id}</span>
                    )}
                    {h.created_at && (
                      <span className="historical-date">{formatDate(h.created_at)}</span>
                    )}
                  </div>
                </div>
                <div className="historical-card-intent">{h.intent_summary || h.raw_sql}</div>
                {h.tables && h.tables.length > 0 && (
                  <div className="historical-tables">
                    {h.tables.map(t => <span key={t} className="table-tag">{t}</span>)}
                  </div>
                )}
                {h.decision_reason && (
                  <div className={`historical-reason${isRejected ? ' reason-rejected' : ''}`}>
                    "{h.decision_reason}"
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
