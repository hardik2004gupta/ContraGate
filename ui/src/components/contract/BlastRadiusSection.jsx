import React from 'react'

function fmtNum(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

function BlastNode({ label, rows, type, depth = 0 }) {
  const typeColors = {
    PRIMARY:  { bg: 'var(--violet-dim)', border: 'var(--violet-border)', text: 'var(--violet-light)' },
    CASCADE:  { bg: 'var(--amber-dim)',  border: 'var(--amber-border)',  text: 'var(--amber)' },
    RESTRICT: { bg: 'var(--red-dim)',    border: 'var(--red-border)',    text: 'var(--red)' },
    SET_NULL: { bg: 'var(--blue-dim)',   border: 'var(--blue-border)',   text: 'var(--blue)' },
  }
  const colors = typeColors[type] || typeColors.CASCADE

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: '4px',
    }}>
      <div style={{
        background: colors.bg,
        border: `1px solid ${colors.border}`,
        borderRadius: 'var(--r-md)',
        padding: '10px 16px',
        minWidth: '120px',
        textAlign: 'center',
      }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: '12px', fontWeight: '700', color: colors.text }}>{label}</div>
        <div style={{ fontFamily: 'var(--mono)', fontSize: '18px', fontWeight: '800', color: 'var(--text-0)', margin: '2px 0' }}>{fmtNum(rows)}</div>
        <div style={{ fontSize: '9px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.8px', color: colors.text, opacity: 0.8 }}>{type}</div>
      </div>
    </div>
  )
}

export default function BlastRadiusSection({ contract }) {
  const cascade = contract.cascade || []
  const primaryRows = contract.estimated_primary_rows ?? 0
  const actualPrimary = contract.actual_primary_rows
  const totalRows = contract.blast_radius?.total_rows ??
    (primaryRows + cascade.reduce((s, c) => s + (c.estimated_rows ?? 0), 0))

  const hasTree = cascade.length > 0 && cascade.length <= 6

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Total impact banner */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 18px',
        background: totalRows > 10000
          ? 'var(--red-dim)'
          : totalRows > 1000
          ? 'var(--amber-dim)'
          : 'var(--surface-3)',
        border: `1px solid ${totalRows > 10000 ? 'var(--red-border)' : totalRows > 1000 ? 'var(--amber-border)' : 'var(--border-1)'}`,
        borderRadius: 'var(--r-md)',
      }}>
        <div>
          <div className="tech-label">Total Rows Affected Across All Tables</div>
          <div style={{
            fontFamily: 'var(--mono)',
            fontSize: '32px',
            fontWeight: '800',
            color: totalRows > 10000 ? 'var(--red-light)' : totalRows > 1000 ? 'var(--amber-light)' : 'var(--text-0)',
            letterSpacing: '-1px',
            lineHeight: 1.1,
            marginTop: '4px',
          }}>{fmtNum(totalRows)}</div>
        </div>
        {totalRows > 0 && (
          <div style={{ fontSize: '32px', opacity: 0.4 }}>
            {totalRows > 50000 ? '⚠' : totalRows > 10000 ? '△' : '◷'}
          </div>
        )}
      </div>

      {/* Visual tree (when cascade exists) */}
      {hasTree && (
        <div>
          <div className="tech-label" style={{ marginBottom: '16px' }}>Impact Graph</div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0' }}>
            {/* Primary node */}
            <BlastNode
              label={contract.primary_table || 'primary'}
              rows={actualPrimary ?? primaryRows}
              type="PRIMARY"
            />

            {/* Connector */}
            <div style={{
              width: '2px',
              height: '20px',
              background: 'var(--border-2)',
              position: 'relative',
            }}>
              <div style={{
                position: 'absolute',
                bottom: 0,
                left: '50%',
                transform: 'translateX(-50%)',
                width: 0,
                height: 0,
                borderLeft: '5px solid transparent',
                borderRight: '5px solid transparent',
                borderTop: '6px solid var(--border-2)',
              }} />
            </div>

            {/* Cascade nodes */}
            {cascade.length === 1 ? (
              <BlastNode
                label={cascade[0].table}
                rows={cascade[0].estimated_rows}
                type={cascade[0].cascade_type || 'CASCADE'}
              />
            ) : (
              <div style={{
                display: 'flex',
                gap: '12px',
                flexWrap: 'wrap',
                justifyContent: 'center',
              }}>
                {cascade.map((c, i) => (
                  <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <BlastNode
                      label={c.table}
                      rows={c.estimated_rows}
                      type={c.cascade_type || 'CASCADE'}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Detailed table */}
      <div>
        {hasTree && <div className="tech-label" style={{ marginBottom: '10px' }}>Impact Table</div>}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Table</th>
                <th>Type</th>
                <th>Estimated Rows</th>
                <th>Actual (Simulation)</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <span className="mono-tag">{contract.primary_table || '—'}</span>
                </td>
                <td>
                  <span className="badge badge-auto" style={{ fontSize: '9px' }}>PRIMARY</span>
                </td>
                <td className="mono text-sm">{fmtNum(primaryRows)}</td>
                <td className="mono text-sm" style={{ color: 'var(--cyan)' }}>
                  {actualPrimary != null ? fmtNum(actualPrimary) : '—'}
                </td>
              </tr>
              {cascade.map((c, i) => (
                <tr key={i}>
                  <td><span className="mono-tag">{c.table}</span></td>
                  <td>
                    <span className="badge badge-standard" style={{ fontSize: '9px' }}>
                      {c.cascade_type || 'CASCADE'}
                    </span>
                  </td>
                  <td className="mono text-sm">{fmtNum(c.estimated_rows)}</td>
                  <td className="mono text-sm" style={{ color: 'var(--cyan)' }}>
                    {c.actual_rows != null ? fmtNum(c.actual_rows) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
