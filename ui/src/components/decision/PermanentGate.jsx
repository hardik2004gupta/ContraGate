import React from 'react'

export default function PermanentGate({ checked, onChange }) {
  return (
    <div style={{
      padding: '16px',
      background: 'var(--purple-dim)',
      border: `2px solid ${checked ? 'var(--purple)' : 'var(--purple-border)'}`,
      borderRadius: 'var(--r-md)',
      transition: 'border-color var(--t-2)',
    }}>
      <label style={{
        display: 'flex',
        gap: '12px',
        alignItems: 'flex-start',
        cursor: 'pointer',
      }}>
        <div style={{ position: 'relative', flexShrink: 0, marginTop: '2px' }}>
          <input
            type="checkbox"
            checked={checked}
            onChange={e => onChange(e.target.checked)}
            style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
          />
          <div style={{
            width: '18px',
            height: '18px',
            borderRadius: '4px',
            border: `2px solid ${checked ? 'var(--purple)' : 'var(--purple-border)'}`,
            background: checked ? 'var(--purple)' : 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'all var(--t-2)',
            cursor: 'pointer',
          }}>
            {checked && (
              <svg width="11" height="8" viewBox="0 0 11 8" fill="none">
                <path d="M1 4L4 7L10 1" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
          </div>
        </div>
        <div style={{
          fontSize: '12px',
          lineHeight: '1.6',
          color: checked ? 'var(--text-0)' : 'var(--text-1)',
          fontWeight: checked ? '500' : '400',
          transition: 'color var(--t-2)',
        }}>
          I HAVE READ AND UNDERSTOOD THAT THIS ACTION CANNOT BE AUTOMATICALLY REVERSED
        </div>
      </label>
    </div>
  )
}
