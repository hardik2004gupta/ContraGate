import React from 'react'

const FLAG_CONFIGS = {
  POLICY_DDL_NO_BACKUP: {
    severity: 'critical',
    icon: '🔴',
    label: 'No Confirmed Backup',
    rule: 'POLICY_DDL_NO_BACKUP',
  },
  POLICY_PII_STANDARD_REVIEW: {
    severity: 'warning',
    icon: '🟡',
    label: 'PII Table Operation',
    rule: 'POLICY_PII_STANDARD_REVIEW',
  },
  POLICY_EXTERNAL_INPUT: {
    severity: 'critical',
    icon: '🔴',
    label: 'External Input Source',
    rule: 'POLICY_EXTERNAL_INPUT',
  },
  POLICY_BULK_DELETE_SENSITIVE: {
    severity: 'critical',
    icon: '🔴',
    label: 'Bulk Delete on Sensitive Table',
    rule: 'POLICY_BULK_DELETE_SENSITIVE',
  },
  POLICY_PAYMENT_WEBHOOK: {
    severity: 'critical',
    icon: '🔴',
    label: 'Payment Webhook Detected',
    rule: 'POLICY_PAYMENT_WEBHOOK',
  },
  POLICY_AFTER_HOURS: {
    severity: 'warning',
    icon: '🟡',
    label: 'After-Hours Submission',
    rule: 'POLICY_AFTER_HOURS',
  },
  POLICY_AUTO_REJECT_PATTERN: {
    severity: 'critical',
    icon: '🔴',
    label: 'Matches Auto-Rejected Pattern',
    rule: 'POLICY_AUTO_REJECT_PATTERN',
  },
  POLICY_PII_EXPENSIVE_READ: {
    severity: 'warning',
    icon: '🟡',
    label: 'Expensive Read on PII Table',
    rule: 'POLICY_PII_EXPENSIVE_READ',
  },
}

const SEV_STYLES = {
  critical: {
    bg: 'var(--red-dim)',
    border: 'var(--red-border)',
    accent: 'var(--red)',
    label: 'CRITICAL',
    badgeBg: 'var(--red)',
  },
  warning: {
    bg: 'var(--amber-dim)',
    border: 'var(--amber-border)',
    accent: 'var(--amber)',
    label: 'WARNING',
    badgeBg: 'var(--amber)',
  },
  info: {
    bg: 'var(--surface-2)',
    border: 'var(--border-1)',
    accent: 'var(--blue)',
    label: 'INFO',
    badgeBg: 'var(--blue)',
  },
}

function FlagItem({ flag, index }) {
  const key = typeof flag === 'string' ? flag : flag?.rule_id || ''
  const cfg = FLAG_CONFIGS[key] || {
    severity: 'warning',
    icon: '⚠',
    label: typeof flag === 'string' ? flag : key || 'Policy Flag',
    rule: key,
  }
  const sev = SEV_STYLES[cfg.severity] || SEV_STYLES.warning
  const reason = typeof flag === 'object' ? flag?.reason || flag?.message : null

  return (
    <div style={{
      display: 'flex',
      gap: '12px',
      padding: '12px 14px',
      background: sev.bg,
      border: `1px solid ${sev.border}`,
      borderLeft: `3px solid ${sev.accent}`,
      borderRadius: 'var(--r-md)',
      animation: 'fadeIn 200ms ease-out both',
      animationDelay: `${index * 50}ms`,
    }}>
      <span style={{ fontSize: '16px', flexShrink: 0, marginTop: '1px' }}>{cfg.icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: reason ? '4px' : 0 }}>
          <span style={{
            fontSize: '13px',
            fontWeight: '600',
            color: 'var(--text-0)',
          }}>{cfg.label}</span>
          <span style={{
            fontSize: '9px',
            fontWeight: '800',
            color: '#fff',
            background: sev.badgeBg,
            padding: '1px 6px',
            borderRadius: '3px',
            letterSpacing: '0.5px',
            textTransform: 'uppercase',
          }}>{sev.label}</span>
          {cfg.rule && (
            <span style={{
              fontSize: '10px',
              color: 'var(--text-3)',
              fontFamily: 'var(--mono)',
            }}>{cfg.rule}</span>
          )}
        </div>
        {reason && (
          <div style={{ fontSize: '12px', color: 'var(--text-2)', lineHeight: '1.5' }}>
            {reason}
          </div>
        )}
      </div>
    </div>
  )
}

export default function FlagsSection({ contract }) {
  const violations = contract.policy_violations || []
  const hasInjectionRisk = contract.prompt_injection_risk === true
  const simUnavail = contract.simulation?.simulation_available === false
  const simTimeout = contract.simulation?.timeout_occurred === true
  const retrievalUnavail = contract.retrieval_results?.retrieval_available === false
  const confidenceNote = contract.blast_radius_confidence_reason

  const hasAnyFlag = violations.length > 0 || hasInjectionRisk || simUnavail || simTimeout || retrievalUnavail || confidenceNote

  if (!hasAnyFlag) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '24px 16px',
        gap: '6px',
        color: 'var(--text-3)',
        textAlign: 'center',
      }}>
        <span style={{ fontSize: '20px', opacity: 0.6 }}>✓</span>
        <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--green)' }}>No flags raised</div>
        <div style={{ fontSize: '12px' }}>No policy violations or system warnings for this operation.</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {/* Policy violations */}
      {violations.map((v, i) => (
        <FlagItem key={i} flag={v} index={i} />
      ))}

      {/* Prompt injection risk */}
      {hasInjectionRisk && (
        <div style={{
          display: 'flex',
          gap: '12px',
          padding: '12px 14px',
          background: 'var(--purple-dim)',
          border: '1px solid var(--purple-border)',
          borderLeft: '3px solid var(--purple)',
          borderRadius: 'var(--r-md)',
        }}>
          <span style={{ fontSize: '16px', flexShrink: 0 }}>🟣</span>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-0)' }}>
                Prompt Injection Risk
              </span>
              <span style={{
                fontSize: '9px',
                fontWeight: '800',
                color: '#fff',
                background: 'var(--purple)',
                padding: '1px 6px',
                borderRadius: '3px',
                letterSpacing: '0.5px',
                textTransform: 'uppercase',
              }}>CRITICAL</span>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-2)', lineHeight: '1.5' }}>
              This operation originated from an external or user-supplied input source.
              SQL has been treated as untrusted data and sandboxed from LLM instruction paths.
            </div>
          </div>
        </div>
      )}

      {/* Simulation warning */}
      {(simUnavail || simTimeout) && (
        <div style={{
          display: 'flex',
          gap: '12px',
          padding: '12px 14px',
          background: 'var(--amber-dim)',
          border: '1px solid var(--amber-border)',
          borderLeft: '3px solid var(--amber)',
          borderRadius: 'var(--r-md)',
        }}>
          <span style={{ fontSize: '16px', flexShrink: 0 }}>⚙</span>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-0)' }}>
                {simTimeout ? 'Sandbox Simulation Timed Out' : 'Sandbox Simulation Unavailable'}
              </span>
              <span style={{
                fontSize: '9px',
                fontWeight: '800',
                color: '#fff',
                background: 'var(--amber)',
                padding: '1px 6px',
                borderRadius: '3px',
                letterSpacing: '0.5px',
                textTransform: 'uppercase',
              }}>WARNING</span>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-2)', lineHeight: '1.5' }}>
              {simTimeout
                ? 'Simulation timed out after 2 attempts with exponential backoff. Row counts are statistical estimates only.'
                : 'Sandbox was unavailable. Row counts shown are statistical estimates from pg_stat, not confirmed simulation results.'}
            </div>
          </div>
        </div>
      )}

      {/* Memory retrieval warning */}
      {retrievalUnavail && (
        <div style={{
          display: 'flex',
          gap: '12px',
          padding: '12px 14px',
          background: 'var(--surface-2)',
          border: '1px solid var(--border-1)',
          borderLeft: '3px solid var(--blue)',
          borderRadius: 'var(--r-md)',
        }}>
          <span style={{ fontSize: '16px', flexShrink: 0 }}>📭</span>
          <div>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-0)', marginBottom: '4px' }}>
              Historical Memory Unavailable
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-2)', lineHeight: '1.5' }}>
              The memory store was unreachable. No historical precedents were retrieved.
              This operation is approved without institutional context.
            </div>
          </div>
        </div>
      )}

      {/* Confidence note */}
      {confidenceNote && (
        <div style={{
          display: 'flex',
          gap: '12px',
          padding: '12px 14px',
          background: 'var(--surface-2)',
          border: '1px solid var(--amber-border)',
          borderLeft: '3px solid var(--amber)',
          borderRadius: 'var(--r-md)',
        }}>
          <span style={{ fontSize: '16px', flexShrink: 0 }}>📉</span>
          <div>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-0)', marginBottom: '4px' }}>
              Blast Radius Confidence Reduced
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-2)', lineHeight: '1.5' }}>
              {confidenceNote}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
