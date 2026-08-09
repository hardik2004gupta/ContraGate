import React, { useState } from 'react'

const sectionStyles = `
  .contract-section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    cursor: pointer;
    user-select: none;
  }

  .section-header:hover { background: var(--surface-2); }

  .section-number {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .section-num-badge {
    width: 22px; height: 22px;
    background: var(--text-primary);
    color: #fff;
    border-radius: 50%;
    font-size: 11px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .section-question {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
  }

  .section-chevron {
    font-size: 12px;
    color: var(--text-tertiary);
    transition: transform 0.2s;
  }

  .section-chevron.open { transform: rotate(90deg); }

  .section-body {
    padding: 18px;
  }
`

export default function ContractSection({ number, question, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <>
      <style>{sectionStyles}</style>
      <div className="contract-section">
        <div className="section-header" onClick={() => setOpen(o => !o)}>
          <div className="section-number">
            <div className="section-num-badge">{number}</div>
            <div className="section-question">{question}</div>
          </div>
          <span className={`section-chevron${open ? ' open' : ''}`}>▶</span>
        </div>
        {open && <div className="section-body">{children}</div>}
      </div>
    </>
  )
}
