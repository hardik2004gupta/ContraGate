import React from 'react'

const gateStyles = `
  .permanent-gate {
    background: var(--permanent-bg);
    border: 1.5px solid #C4B5FD;
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    margin-top: 12px;
  }

  .permanent-gate-label {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    cursor: pointer;
    user-select: none;
  }

  .permanent-gate-checkbox {
    width: 16px;
    height: 16px;
    margin-top: 1px;
    flex-shrink: 0;
    cursor: pointer;
    accent-color: var(--permanent);
  }

  .permanent-gate-text {
    font-size: 13px;
    font-weight: 600;
    color: var(--permanent);
    line-height: 1.5;
    letter-spacing: 0.1px;
  }

  .permanent-gate-warning {
    margin-top: 8px;
    font-size: 12px;
    color: var(--permanent);
    opacity: 0.8;
    padding-left: 26px;
  }
`

/**
 * PermanentGate — acknowledgement checkbox for PERMANENT operations.
 *
 * Must be checked before the Approve button activates.
 * Only rendered when contract.reversibility === "PERMANENT".
 */
export default function PermanentGate({ checked, onChange }) {
  return (
    <>
      <style>{gateStyles}</style>
      <div className="permanent-gate">
        <label className="permanent-gate-label">
          <input
            type="checkbox"
            className="permanent-gate-checkbox"
            checked={checked}
            onChange={e => onChange(e.target.checked)}
          />
          <span className="permanent-gate-text">
            I HAVE READ AND UNDERSTOOD THAT THIS ACTION CANNOT BE AUTOMATICALLY REVERSED
          </span>
        </label>
        <div className="permanent-gate-warning">
          This operation has permanent components that cannot be recovered without a manual restore.
          The Approve button will remain disabled until this box is checked.
        </div>
      </div>
    </>
  )
}
