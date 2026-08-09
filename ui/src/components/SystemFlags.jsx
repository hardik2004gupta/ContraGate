import React from 'react'

const flagStyles = `
  .flags-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .flag-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 12px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    line-height: 1.5;
  }

  .flag-item.flag-danger {
    background: var(--full-bg);
    border: 1px solid #FECACA;
    color: #7F1D1D;
  }

  .flag-item.flag-warning {
    background: var(--standard-bg);
    border: 1px solid #FDE68A;
    color: #78350F;
  }

  .flag-item.flag-info {
    background: #DBEAFE;
    border: 1px solid #BFDBFE;
    color: #1E3A8A;
  }

  .flag-item.flag-neutral {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text-secondary);
  }

  .flag-icon { font-size: 14px; flex-shrink: 0; margin-top: 1px; }

  .flag-title {
    font-weight: 600;
    margin-bottom: 2px;
  }

  .flag-desc {
    font-size: 12px;
    opacity: 0.85;
  }

  .confidence-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .confidence-bar-track {
    flex: 1;
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }

  .confidence-bar-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.3s;
  }

  .confidence-value {
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 600;
    min-width: 36px;
    text-align: right;
  }

  .no-flags {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-tertiary);
    padding: 12px 0;
  }
`

function confidenceColor(score) {
  if (score >= 0.75) return '#059669'
  if (score >= 0.5) return '#D97706'
  return '#DC2626'
}

/**
 * SystemFlags — Section 4 of the consequence contract.
 * Displays policy violations, injection risk, simulation warnings, confidence.
 */
export default function SystemFlags({ contract }) {
  const flags = []

  // Prompt injection risk
  if (contract.prompt_injection_risk) {
    flags.push({
      level: 'danger',
      icon: '🚨',
      title: 'Prompt Injection Risk',
      desc: 'This operation originated from external user input. The SQL has been treated as untrusted data throughout the pipeline.',
    })
  }

  // Policy violations
  if (contract.policy_violations?.length > 0) {
    contract.policy_violations.forEach(v => {
      flags.push({
        level: 'danger',
        icon: '🚫',
        title: `Policy Violation: ${v.rule_id || v.rule}`,
        desc: v.description || v.reason || v.message || JSON.stringify(v),
      })
    })
  }

  // Historical rejection warning
  const rejectedHistory = (contract.historical_precedents || []).filter(
    h => h.outcome === 'REJECTED' || h.outcome === 'ROLLED_BACK'
  )
  if (rejectedHistory.length > 0) {
    flags.push({
      level: 'warning',
      icon: '⚠',
      title: `${rejectedHistory.length} prior rejection(s) for similar operations`,
      desc: rejectedHistory[0].decision_reason
        ? `Most recent: "${rejectedHistory[0].decision_reason}"`
        : `${rejectedHistory.length} historically similar operations were rejected or rolled back.`,
    })
  }

  // Simulation unavailable
  if (contract.simulation?.simulation_available === false) {
    flags.push({
      level: 'warning',
      icon: '⚙',
      title: 'Simulation Unavailable',
      desc: 'The staging sandbox timed out. Actual row counts from simulation are not available — estimates are from query planner statistics only.',
    })
  }

  // External triggers not simulatable
  if (contract.blast_radius?.external_triggers?.length > 0) {
    flags.push({
      level: 'warning',
      icon: '🔌',
      title: 'External Triggers Cannot Be Simulated',
      desc: `${contract.blast_radius.external_triggers.length} non-transactional extension trigger(s) detected (e.g., pg_net, dblink). These will fire on approval and cannot be rolled back.`,
    })
  }

  // Retrieval unavailable
  if (contract.retrieval_results?.retrieval_available === false) {
    flags.push({
      level: 'info',
      icon: '📭',
      title: 'Memory Store Unavailable',
      desc: 'Historical context was not loaded for this operation. The memory store was unavailable during pipeline execution.',
    })
  }

  // Sandbox timeout
  if (contract.simulation?.timeout_occurred) {
    flags.push({
      level: 'warning',
      icon: '⏱',
      title: 'Sandbox Timeout',
      desc: 'Simulation timed out after 2 attempts with exponential backoff. Row counts shown are statistical estimates.',
    })
  }

  // Confidence score
  const confidence = contract.row_confidence ?? null

  return (
    <>
      <style>{flagStyles}</style>
      {flags.length === 0 && confidence === null ? (
        <div className="no-flags">✅ No flags raised by the system for this operation.</div>
      ) : (
        <div className="flags-list">
          {flags.map((f, i) => (
            <div key={i} className={`flag-item flag-${f.level}`}>
              <span className="flag-icon">{f.icon}</span>
              <div>
                <div className="flag-title">{f.title}</div>
                {f.desc && <div className="flag-desc">{f.desc}</div>}
              </div>
            </div>
          ))}

          {confidence !== null && (
            <div className="flag-item flag-neutral">
              <span className="flag-icon">📊</span>
              <div style={{ flex: 1 }}>
                <div className="flag-title">Blast Radius Confidence</div>
                <div className="confidence-row" style={{ marginTop: 6 }}>
                  <div className="confidence-bar-track">
                    <div
                      className="confidence-bar-fill"
                      style={{
                        width: `${Math.round(confidence * 100)}%`,
                        background: confidenceColor(confidence),
                      }}
                    />
                  </div>
                  <span className="confidence-value" style={{ color: confidenceColor(confidence) }}>
                    {Math.round(confidence * 100)}%
                  </span>
                </div>
                {confidence < 0.75 && (
                  <div className="flag-desc" style={{ marginTop: 4 }}>
                    Confidence is below 75% — statistics may be stale for this table.
                    {confidence < 0.5 && ' Prior inaccuracy has reduced this score.'}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )
}
