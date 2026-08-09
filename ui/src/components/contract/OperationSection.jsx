import React, { useState } from 'react'

function fmtNum(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

function highlightSQL(sql) {
  if (!sql) return ''
  const keywords = /\b(SELECT|FROM|WHERE|DELETE|UPDATE|INSERT|INTO|SET|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AND|OR|NOT|IN|IS|NULL|VALUES|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|AS|DISTINCT|UNION|ALL|DROP|TABLE|ALTER|CREATE|TRUNCATE|INDEX|COLUMN|ADD|CASCADE|CONSTRAINT|PRIMARY|KEY|FOREIGN|REFERENCES)\b/gi
  const escaped = sql
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return escaped.replace(keywords, m => `<span class="code-keyword">${m.toUpperCase()}</span>`)
}

export default function OperationSection({ contract }) {
  const [copied, setCopied] = useState(false)

  const copySQL = () => {
    if (!contract.raw_sql) return
    navigator.clipboard.writeText(contract.raw_sql).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }

  const primaryRows = contract.estimated_primary_rows ?? 0
  const actualRows = contract.actual_primary_rows
  const cascade = contract.cascade || []
  const externalTriggers = contract.blast_radius?.external_triggers || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* SQL block */}
      {contract.raw_sql && (
        <div>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '8px',
          }}>
            <span className="tech-label">SQL Statement</span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={copySQL}
              style={{ fontSize: '11px', padding: '3px 8px' }}
            >
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </div>
          <div
            className="sql-block"
            dangerouslySetInnerHTML={{ __html: highlightSQL(contract.raw_sql) }}
          />
        </div>
      )}

      {/* Metrics */}
      <div className="metric-row">
        <div className="metric-tile">
          <div className="metric-value">{fmtNum(primaryRows)}</div>
          <div className="metric-label">Est. Rows</div>
        </div>
        {actualRows != null && (
          <div className="metric-tile">
            <div className="metric-value" style={{ color: 'var(--cyan)' }}>{fmtNum(actualRows)}</div>
            <div className="metric-label">Actual (Sim)</div>
          </div>
        )}
        {cascade.length > 0 && (
          <div className="metric-tile">
            <div className="metric-value" style={{ color: 'var(--amber)' }}>{cascade.length}</div>
            <div className="metric-label">Cascade Tables</div>
          </div>
        )}
        {externalTriggers.length > 0 && (
          <div className="metric-tile" style={{ background: 'var(--red-dim)', borderColor: 'var(--red-border)' }}>
            <div className="metric-value" style={{ color: 'var(--red)' }}>{externalTriggers.length}</div>
            <div className="metric-label">Ext. Triggers</div>
          </div>
        )}
      </div>

      {/* Metadata grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: '8px',
      }}>
        {contract.operation_type && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            <span className="tech-label">Operation Type</span>
            <span className="mono-tag" style={{ width: 'fit-content' }}>{contract.operation_type}</span>
          </div>
        )}
        {contract.primary_table && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            <span className="tech-label">Primary Table</span>
            <span className="mono-tag" style={{ width: 'fit-content' }}>{contract.primary_table}</span>
          </div>
        )}
        {contract.submitted_by && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            <span className="tech-label">Submitted By</span>
            <span style={{ fontSize: '12px', color: 'var(--text-1)' }}>{contract.submitted_by}</span>
          </div>
        )}
        {contract.source_type && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            <span className="tech-label">Source Type</span>
            <span style={{ fontSize: '12px', color: 'var(--text-1)' }}>{contract.source_type}</span>
          </div>
        )}
      </div>

      {/* External triggers */}
      {externalTriggers.length > 0 && (
        <div>
          <div className="tech-label" style={{ marginBottom: '8px' }}>
            External Actions That Will Fire ({externalTriggers.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {externalTriggers.map((t, i) => (
              <div key={i} style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 12px',
                background: 'var(--red-dim)',
                border: '1px solid var(--red-border)',
                borderRadius: 'var(--r-sm)',
                fontSize: '12px',
              }}>
                <span style={{ color: 'var(--red)', fontSize: '11px', fontWeight: '700', flexShrink: 0 }}>TRIGGER</span>
                <span style={{ color: 'var(--text-1)' }}>{t.trigger_name || t.name || JSON.stringify(t)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
