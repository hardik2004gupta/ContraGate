import React from 'react'
import { useNavigate } from 'react-router-dom'
import { RiskTierBadge } from '../shared/Badge.jsx'

const OP_ICONS = {
  DELETE: { icon: '🗑', color: 'var(--red)' },
  UPDATE: { icon: '✏', color: 'var(--amber)' },
  INSERT: { icon: '＋', color: 'var(--green)' },
  SELECT: { icon: '⊙', color: 'var(--blue)' },
  DROP:   { icon: '☠', color: 'var(--red)' },
  ALTER:  { icon: '⚙', color: 'var(--purple)' },
  TRUNCATE: { icon: '⊘', color: 'var(--red)' },
  CREATE: { icon: '✦', color: 'var(--cyan)' },
}

const EDGE_COLORS = {
  FULL_CONTRACT:   'var(--red)',
  STANDARD_REVIEW: 'var(--amber)',
  AUTO_EXECUTE:    'var(--green)',
}

function formatCountdown(s) {
  if (s <= 0) return { text: 'Timed out', cls: 'countdown-urgent' }
  const m = Math.floor(s / 60)
  const sec = s % 60
  const text = m > 0 ? `${m}m ${String(sec).padStart(2,'0')}s` : `${sec}s`
  const cls = s < 120 ? 'countdown-urgent' : s < 300 ? 'countdown-warn' : 'countdown-ok'
  return { text, cls }
}

function fmtNum(n) {
  if (n == null || n === 0) return '—'
  return Number(n).toLocaleString()
}

function extractOpType(item) {
  if (item.operation_type) return item.operation_type.toUpperCase()
  if (item.raw_sql) {
    const first = item.raw_sql.trim().split(/\s+/)[0].toUpperCase()
    return first
  }
  return 'SQL'
}

export default function ApprovalCard({ item }) {
  const navigate = useNavigate()
  const opType = extractOpType(item)
  const opMeta = OP_ICONS[opType] || { icon: '◈', color: 'var(--violet)' }
  const edgeColor = EDGE_COLORS[item.risk_tier] || 'var(--border-2)'
  const { text: cdText, cls: cdCls } = formatCountdown(item.seconds_remaining ?? 0)
  const isPermanent = item.reversibility === 'PERMANENT'
  const hasInjection = item.prompt_injection_risk
  const hasRejection = item.historical_rejection_count > 0
  const extTriggers = item.external_trigger_count ?? 0

  return (
    <div
      onClick={() => navigate(`/approval/${item.approval_id}`)}
      style={{
        background: 'var(--surface-2)',
        border: '1px solid var(--border-1)',
        borderLeft: `3px solid ${edgeColor}`,
        borderRadius: 'var(--r-lg)',
        padding: '20px',
        cursor: 'pointer',
        transition: 'background var(--t-1), box-shadow var(--t-1), border-color var(--t-1)',
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
        animation: 'fadeIn 200ms ease-out both',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.background = 'var(--surface-3)'
        e.currentTarget.style.boxShadow = `var(--shadow-md), -3px 0 12px ${edgeColor}33`
        e.currentTarget.style.borderColor = 'var(--border-2)'
        e.currentTarget.style.borderLeftColor = edgeColor
      }}
      onMouseLeave={e => {
        e.currentTarget.style.background = 'var(--surface-2)'
        e.currentTarget.style.boxShadow = ''
        e.currentTarget.style.borderColor = 'var(--border-1)'
        e.currentTarget.style.borderLeftColor = edgeColor
      }}
    >
      {/* Top row: badges + countdown */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
          <RiskTierBadge tier={item.risk_tier} />
          {isPermanent && (
            <span className="badge badge-permanent">⚠ Permanent</span>
          )}
          {hasInjection && (
            <span className="badge badge-injection">🚨 Injection Risk</span>
          )}
        </div>
        <span className={`countdown ${cdCls}`} style={{ flexShrink: 0 }}>{cdText}</span>
      </div>

      {/* Operation + table */}
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '4px' }}>
          <span style={{ fontSize: '18px' }}>{opMeta.icon}</span>
          <span style={{
            fontSize: '22px',
            fontWeight: '800',
            fontFamily: 'var(--mono)',
            color: opMeta.color,
            letterSpacing: '-0.5px',
          }}>{opType}</span>
          <span style={{
            fontSize: '16px',
            fontWeight: '700',
            fontFamily: 'var(--mono)',
            color: 'var(--text-0)',
          }}>{item.primary_table || '—'}</span>
        </div>
        {item.intent_summary && (
          <div style={{
            fontSize: '12px',
            color: 'var(--text-2)',
            lineHeight: '1.5',
            overflow: 'hidden',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
          }}>{item.intent_summary}</div>
        )}
      </div>

      {/* Impact metrics */}
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        <div className="metric-tile" style={{ flex: '1', minWidth: '80px' }}>
          <div className="metric-value-sm">{fmtNum(item.estimated_rows)}</div>
          <div className="metric-label">Est. Rows</div>
        </div>

        {isPermanent && (
          <div className="metric-tile" style={{
            flex: '1', minWidth: '80px',
            background: 'var(--purple-dim)',
            borderColor: 'var(--purple-border)',
          }}>
            <div className="metric-value-sm" style={{ color: 'var(--purple-light)' }}>PERM</div>
            <div className="metric-label">Reversibility</div>
          </div>
        )}

        {!isPermanent && item.reversibility && (
          <div className="metric-tile" style={{ flex: '1', minWidth: '80px' }}>
            <div className="metric-value-sm" style={{
              fontSize: '11px',
              color: 'var(--green)',
              letterSpacing: '0.2px',
            }}>{item.reversibility.replace('REVERSIBLE_', '').replace('_', ' ')}</div>
            <div className="metric-label">Reversibility</div>
          </div>
        )}

        {extTriggers > 0 && (
          <div className="metric-tile" style={{
            flex: '1', minWidth: '80px',
            background: 'var(--amber-dim)',
            borderColor: 'var(--amber-border)',
          }}>
            <div className="metric-value-sm" style={{ color: 'var(--amber)' }}>{extTriggers}</div>
            <div className="metric-label">Ext. Effects</div>
          </div>
        )}
      </div>

      {/* Warnings */}
      {(hasRejection) && (
        <div style={{
          fontSize: '11px',
          color: 'var(--red-light)',
          background: 'var(--red-dim)',
          border: '1px solid var(--red-border)',
          borderRadius: 'var(--r-sm)',
          padding: '6px 10px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}>
          ⚠ {item.historical_rejection_count} prior rejection{item.historical_rejection_count > 1 ? 's' : ''} for similar operations
        </div>
      )}

      {/* CTA */}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          className="btn btn-secondary btn-sm"
          onClick={e => { e.stopPropagation(); navigate(`/approval/${item.approval_id}`) }}
          style={{ fontSize: '12px', fontWeight: '600' }}
        >
          Review Consequences →
        </button>
      </div>
    </div>
  )
}
