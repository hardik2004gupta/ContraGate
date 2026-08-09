import React from 'react'

function fmtNum(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

function DeltaRow({ table, before, after, type = 'PRIMARY' }) {
  const delta = (before != null && after != null) ? after - before : null
  const deltaText = delta != null
    ? (delta === 0 ? 'No change' : `${delta < 0 ? '' : '+'}${delta.toLocaleString()}`)
    : '—'
  const deltaColor = delta == null ? 'var(--text-3)'
    : delta < 0 ? 'var(--red)' : delta > 0 ? 'var(--amber)' : 'var(--text-2)'

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 120px 120px 100px',
      gap: '8px',
      padding: '10px 14px',
      borderBottom: '1px solid var(--border-0)',
      alignItems: 'center',
      fontSize: '13px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span className="mono-tag">{table}</span>
        {type === 'PRIMARY' && (
          <span className="badge badge-auto" style={{ fontSize: '9px' }}>PRIMARY</span>
        )}
      </div>
      <div className="mono text-sm" style={{ color: 'var(--text-1)' }}>{fmtNum(before)}</div>
      <div className="mono text-sm" style={{ color: 'var(--cyan)' }}>{fmtNum(after)}</div>
      <div className="mono text-sm" style={{ color: deltaColor, fontWeight: delta !== 0 ? '700' : '400' }}>
        {deltaText}
      </div>
    </div>
  )
}

export default function SandboxSection({ contract }) {
  const sim = contract.simulation || {}
  const cascade = contract.cascade || []
  const externalCalls = sim.external_calls_logged || []

  if (!sim.simulation_available) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div className="warn-banner">
          ⚙ Sandbox simulation unavailable — row counts shown are statistical estimates only.
          {sim.timeout_occurred && ' Simulation timed out after 2 attempts.'}
        </div>
      </div>
    )
  }

  if (!sim.executed) {
    return (
      <div style={{ color: 'var(--text-2)', fontSize: '13px', padding: '8px 0' }}>
        Simulation was not executed for this operation.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* Safety banner */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '12px 16px',
        background: 'var(--green-dim)',
        border: '1px solid var(--green-border)',
        borderRadius: 'var(--r-md)',
        fontSize: '13px',
        fontWeight: '600',
        color: 'var(--green)',
      }}>
        <span style={{ fontSize: '16px' }}>✓</span>
        SIMULATED · NO PRODUCTION DATA MODIFIED
        <span style={{
          marginLeft: 'auto',
          fontSize: '11px',
          fontWeight: '400',
          color: 'var(--text-2)',
          fontFamily: 'var(--mono)',
        }}>sandbox-only transaction · always rolled back</span>
      </div>

      {/* Before / After table */}
      {(sim.actual_primary_rows != null || cascade.length > 0) && (
        <div style={{
          background: 'var(--surface-1)',
          border: '1px solid var(--border-1)',
          borderRadius: 'var(--r-md)',
          overflow: 'hidden',
        }}>
          {/* Header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 120px 120px 100px',
            gap: '8px',
            padding: '10px 14px',
            borderBottom: '1px solid var(--border-1)',
            fontSize: '10px',
            fontWeight: '700',
            textTransform: 'uppercase',
            letterSpacing: '0.8px',
            color: 'var(--text-3)',
          }}>
            <div>Table</div>
            <div>Before</div>
            <div style={{ color: 'var(--cyan)' }}>After (Sim)</div>
            <div>Delta</div>
          </div>

          <DeltaRow
            table={contract.primary_table || 'primary'}
            before={contract.estimated_primary_rows}
            after={sim.actual_primary_rows}
            type="PRIMARY"
          />
          {cascade.map((c, i) => (
            <DeltaRow
              key={i}
              table={c.table}
              before={c.estimated_rows}
              after={c.actual_rows}
              type={c.cascade_type || 'CASCADE'}
            />
          ))}
        </div>
      )}

      {/* External calls that would have fired */}
      {externalCalls.length > 0 && (
        <div>
          <div className="tech-label" style={{ marginBottom: '8px' }}>
            External Calls That Would Have Fired ({externalCalls.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {externalCalls.map((call, i) => (
              <div key={i} style={{
                display: 'flex',
                gap: '10px',
                padding: '8px 12px',
                background: 'var(--amber-dim)',
                border: '1px solid var(--amber-border)',
                borderRadius: 'var(--r-sm)',
                fontSize: '12px',
              }}>
                <span style={{ color: 'var(--amber)', fontWeight: '700', flexShrink: 0 }}>TRIGGER</span>
                <span style={{ color: 'var(--text-1)', fontFamily: 'var(--mono)' }}>
                  {call.trigger_name || call.function || JSON.stringify(call)}
                </span>
                <span style={{ color: 'var(--text-3)', marginLeft: 'auto', flexShrink: 0 }}>logged only</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
