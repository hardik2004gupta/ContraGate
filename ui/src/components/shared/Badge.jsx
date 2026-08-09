import React from 'react'

const RISK_MAP = {
  AUTO_EXECUTE:    { cls: 'badge-auto',     label: 'Auto Execute' },
  STANDARD_REVIEW: { cls: 'badge-standard', label: 'Standard Review' },
  FULL_CONTRACT:   { cls: 'badge-full',     label: 'Full Contract' },
}

const OUTCOME_MAP = {
  APPROVED_EXECUTED:       { cls: 'badge-approved',      label: 'Approved' },
  APPROVED_EXECUTION_FAILED: { cls: 'badge-rejected',    label: 'Exec Failed' },
  REJECTED:                { cls: 'badge-rejected',      label: 'Rejected' },
  AUTO_REJECTED:           { cls: 'badge-rejected',      label: 'Auto-Rejected' },
  ROLLED_BACK:             { cls: 'badge-rolled-back',   label: 'Rolled Back' },
  MODIFIED:                { cls: 'badge-modified',      label: 'Modified' },
  TIMED_OUT:               { cls: 'badge-rejected',      label: 'Timed Out' },
  AUTO_EXECUTED:           { cls: 'badge-auto-executed', label: 'Auto-Executed' },
}

const REV_MAP = {
  PERMANENT:            { cls: 'badge-permanent', label: 'Permanent' },
  PARTIAL:              { cls: 'badge-partial',   label: 'Partial' },
  REVERSIBLE_AUTOMATED: { cls: 'badge-reversible', label: 'Reversible' },
  REVERSIBLE_PITR:      { cls: 'badge-reversible', label: 'Reversible (PITR)' },
}

export function RiskTierBadge({ tier, showDot = true }) {
  const { cls, label } = RISK_MAP[tier] || { cls: 'badge-unknown', label: tier || 'Unknown' }
  return (
    <span className={`badge ${cls}`}>
      {showDot && <span className="badge-dot" />}
      {label}
    </span>
  )
}

export function OutcomeBadge({ outcome }) {
  const { cls, label } = OUTCOME_MAP[outcome] || { cls: 'badge-unknown', label: outcome || 'Unknown' }
  return <span className={`badge ${cls}`}>{label}</span>
}

export function ReversibilityBadge({ rev }) {
  const { cls, label } = REV_MAP[rev] || { cls: 'badge-unknown', label: rev || 'Unknown' }
  return <span className={`badge ${cls}`}>{label}</span>
}
