import React from 'react'

const REV_CONFIG = {
  PERMANENT: {
    color: 'var(--purple)',
    bg: 'var(--purple-dim)',
    border: 'var(--purple-border)',
    label: 'PERMANENT',
    icon: '🔴',
    headline: 'No Automated Recovery Path',
    desc: 'This operation cannot be undone without manual intervention or a point-in-time database restore.',
  },
  PARTIAL: {
    color: 'var(--amber)',
    bg: 'var(--amber-dim)',
    border: 'var(--amber-border)',
    label: 'PARTIAL',
    icon: '🟡',
    headline: 'Some Components Cannot Be Recovered',
    desc: 'Parts of this operation are reversible; others will result in permanent data loss.',
  },
  REVERSIBLE_AUTOMATED: {
    color: 'var(--green)',
    bg: 'var(--green-dim)',
    border: 'var(--green-border)',
    label: 'REVERSIBLE',
    icon: '🟢',
    headline: 'Automated Recovery Available',
    desc: 'A mechanical recovery procedure exists and can be executed if needed.',
  },
  REVERSIBLE_PITR: {
    color: 'var(--green)',
    bg: 'var(--green-dim)',
    border: 'var(--green-border)',
    label: 'REVERSIBLE (PITR)',
    icon: '🟢',
    headline: 'Point-in-Time Recovery Available',
    desc: 'This operation can be recovered via database point-in-time restore.',
  },
}

export default function ReversibilitySection({ contract }) {
  const rev = contract.reversibility
  const cfg = REV_CONFIG[rev] || {
    color: 'var(--text-2)',
    bg: 'var(--surface-3)',
    border: 'var(--border-2)',
    label: rev || 'UNKNOWN',
    icon: '⚪',
    headline: 'Classification Unavailable',
    desc: '',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* Hero classification */}
      <div style={{
        background: cfg.bg,
        border: `1px solid ${cfg.border}`,
        borderRadius: 'var(--r-lg)',
        padding: '20px',
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          marginBottom: '8px',
        }}>
          <span style={{ fontSize: '20px' }}>{cfg.icon}</span>
          <div>
            <div style={{
              fontSize: '18px',
              fontWeight: '800',
              color: cfg.color,
              letterSpacing: '1px',
              fontFamily: 'var(--mono)',
            }}>{cfg.label}</div>
            <div style={{ fontSize: '13px', fontWeight: '600', color: 'var(--text-1)', marginTop: '2px' }}>
              {cfg.headline}
            </div>
          </div>
        </div>

        {/* Separator */}
        <div style={{
          height: '1px',
          background: cfg.border,
          margin: '12px 0',
        }} />

        <div style={{ fontSize: '13px', color: 'var(--text-2)', lineHeight: '1.6' }}>
          {contract.reversibility_reason || cfg.desc}
        </div>
      </div>

      {/* Why this classification */}
      {rev === 'PERMANENT' && (
        <div>
          <div className="tech-label" style={{ marginBottom: '10px' }}>Why Permanent?</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {['Operation type has no native rollback',
              contract.cascade?.length > 0 ? 'Cascade deletes detected' : null,
              contract.blast_radius?.external_triggers?.length > 0 ? 'External API calls will fire' : null,
              'No soft-delete column detected',
            ].filter(Boolean).map((reason, i) => (
              <div key={i} style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '13px',
                color: 'var(--text-1)',
              }}>
                <span style={{ color: 'var(--purple)', fontWeight: '700', flexShrink: 0 }}>×</span>
                {reason}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recovery procedure */}
      {contract.rollback_plan && (
        <div>
          <div className="tech-label" style={{ marginBottom: '8px' }}>Recovery Procedure</div>
          <div className="sql-block">{contract.rollback_plan}</div>
        </div>
      )}

      {/* Permanent components */}
      {contract.permanent_components?.length > 0 && (
        <div>
          <div className="tech-label" style={{ marginBottom: '8px' }}>Permanent Components</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {contract.permanent_components.map((p, i) => (
              <div key={i} style={{
                padding: '8px 12px',
                background: 'var(--purple-dim)',
                border: '1px solid var(--purple-border)',
                borderRadius: 'var(--r-sm)',
                fontSize: '12px',
                color: 'var(--purple-light)',
              }}>{p}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
