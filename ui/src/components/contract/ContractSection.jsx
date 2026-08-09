import React, { useState } from 'react'

export default function ContractSection({ number, title, question, children, defaultOpen = true, accent }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div style={{
      background: 'var(--surface-2)',
      border: '1px solid var(--border-1)',
      borderRadius: 'var(--r-lg)',
      overflow: 'hidden',
      boxShadow: 'var(--shadow-xs)',
    }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '14px 20px',
          background: open ? 'var(--surface-3)' : 'var(--surface-2)',
          border: 'none',
          borderBottom: open ? '1px solid var(--border-1)' : 'none',
          cursor: 'pointer',
          color: 'inherit',
          transition: 'background var(--t-1)',
          textAlign: 'left',
          gap: '12px',
        }}
        onMouseEnter={e => { if (!open) e.currentTarget.style.background = 'var(--surface-3)' }}
        onMouseLeave={e => { if (!open) e.currentTarget.style.background = 'var(--surface-2)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            width: '22px',
            height: '22px',
            borderRadius: '50%',
            background: accent ? `${accent}20` : 'var(--violet-dim)',
            border: `1px solid ${accent ? `${accent}50` : 'var(--violet-border)'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '10px',
            fontWeight: '800',
            color: accent || 'var(--violet-light)',
            flexShrink: 0,
          }}>{number}</div>
          <div>
            {title && <div style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text-0)' }}>{title}</div>}
            {question && (
              <div style={{
                fontSize: '13px',
                fontWeight: '600',
                color: 'var(--text-1)',
                letterSpacing: '-0.1px',
              }}>{question}</div>
            )}
          </div>
        </div>
        <span style={{
          fontSize: '11px',
          color: 'var(--text-3)',
          transform: open ? 'rotate(90deg)' : 'none',
          transition: 'transform var(--t-1)',
          flexShrink: 0,
        }}>▶</span>
      </button>

      {open && (
        <div style={{ padding: '20px' }}>
          {children}
        </div>
      )}
    </div>
  )
}
