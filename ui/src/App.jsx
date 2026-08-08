import React from 'react'
import { BrowserRouter, Routes, Route, NavLink, useNavigate } from 'react-router-dom'
import ApprovalQueue from './pages/ApprovalQueue.jsx'
import ContractView from './pages/ContractView.jsx'
import AuditLog from './pages/AuditLog.jsx'

const styles = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #F9FAFB;
    --surface: #FFFFFF;
    --surface-2: #F3F4F6;
    --border: #E5E7EB;
    --border-strong: #D1D5DB;
    --text-primary: #111827;
    --text-secondary: #6B7280;
    --text-tertiary: #9CA3AF;

    --auto: #059669;
    --auto-bg: #D1FAE5;
    --standard: #D97706;
    --standard-bg: #FEF3C7;
    --full: #DC2626;
    --full-bg: #FEE2E2;

    --approved: #059669;
    --approved-bg: #D1FAE5;
    --rejected: #DC2626;
    --rejected-bg: #FEE2E2;
    --rolled-back: #D97706;
    --rolled-back-bg: #FEF3C7;
    --modified: #2563EB;
    --modified-bg: #DBEAFE;
    --auto-executed: #059669;
    --auto-executed-bg: #D1FAE5;

    --permanent: #7C3AED;
    --permanent-bg: #EDE9FE;

    --danger: #DC2626;
    --warning: #D97706;
    --info: #2563EB;

    --nav-width: 200px;
    --radius: 8px;
    --radius-sm: 4px;
    --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 6px rgba(0,0,0,0.07), 0 2px 4px rgba(0,0,0,0.05);
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  }

  body {
    font-family: var(--font);
    background: var(--bg);
    color: var(--text-primary);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }

  .app-shell {
    display: flex;
    min-height: 100vh;
  }

  /* ── Sidebar ────────────────────────────────── */
  .sidebar {
    width: var(--nav-width);
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    position: fixed;
    top: 0; left: 0; bottom: 0;
    z-index: 100;
  }

  .sidebar-brand {
    padding: 20px 16px 16px;
    border-bottom: 1px solid var(--border);
  }

  .sidebar-brand-name {
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.3px;
    color: var(--text-primary);
  }

  .sidebar-brand-tagline {
    font-size: 10px;
    color: var(--text-tertiary);
    margin-top: 2px;
    line-height: 1.3;
  }

  .sidebar-nav {
    flex: 1;
    padding: 8px 0;
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
    text-decoration: none;
    transition: background 0.1s, color 0.1s;
    border-radius: 0;
  }

  .nav-item:hover {
    background: var(--surface-2);
    color: var(--text-primary);
  }

  .nav-item.active {
    background: var(--surface-2);
    color: var(--text-primary);
    border-right: 2px solid var(--text-primary);
  }

  .nav-icon { font-size: 14px; }

  .sidebar-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--border);
    font-size: 11px;
    color: var(--text-tertiary);
  }

  /* ── Main content ───────────────────────────── */
  .main-content {
    margin-left: var(--nav-width);
    flex: 1;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  .page-header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 20px 28px 18px;
  }

  .page-title {
    font-size: 20px;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.3px;
  }

  .page-subtitle {
    font-size: 13px;
    color: var(--text-secondary);
    margin-top: 2px;
  }

  .page-body {
    flex: 1;
    padding: 24px 28px;
  }

  /* ── Cards ──────────────────────────────────── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
  }

  /* ── Risk tier badges ───────────────────────── */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 100px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
  }

  .badge-auto { background: var(--auto-bg); color: var(--auto); }
  .badge-standard { background: var(--standard-bg); color: var(--standard); }
  .badge-full { background: var(--full-bg); color: var(--full); }
  .badge-permanent { background: var(--permanent-bg); color: var(--permanent); }
  .badge-approved { background: var(--approved-bg); color: var(--approved); }
  .badge-rejected { background: var(--rejected-bg); color: var(--rejected); }
  .badge-rolled-back { background: var(--rolled-back-bg); color: var(--rolled-back); }
  .badge-modified { background: var(--modified-bg); color: var(--modified); }
  .badge-auto-executed { background: var(--auto-executed-bg); color: var(--auto-executed); }
  .badge-unknown { background: var(--surface-2); color: var(--text-secondary); }

  /* ── Buttons ────────────────────────────────── */
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    border-radius: var(--radius-sm);
    border: none;
    cursor: pointer;
    transition: opacity 0.1s, background 0.1s;
    font-family: var(--font);
    white-space: nowrap;
  }

  .btn:disabled { opacity: 0.45; cursor: not-allowed; }

  .btn-approve {
    background: #059669;
    color: #fff;
  }
  .btn-approve:hover:not(:disabled) { background: #047857; }

  .btn-reject {
    background: #DC2626;
    color: #fff;
  }
  .btn-reject:hover:not(:disabled) { background: #B91C1C; }

  .btn-modify {
    background: var(--surface);
    color: var(--text-primary);
    border: 1px solid var(--border-strong);
  }
  .btn-modify:hover:not(:disabled) { background: var(--surface-2); }

  .btn-prereq {
    background: var(--surface);
    color: var(--text-secondary);
    border: 1px solid var(--border);
    font-weight: 500;
  }
  .btn-prereq:hover:not(:disabled) { background: var(--surface-2); color: var(--text-primary); }

  .btn-sm { padding: 5px 12px; font-size: 12px; }
  .btn-lg { padding: 10px 20px; font-size: 14px; }

  /* ── Form elements ──────────────────────────── */
  textarea, input[type="text"] {
    font-family: var(--font);
    font-size: 13px;
    color: var(--text-primary);
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    width: 100%;
    resize: vertical;
    outline: none;
    transition: border-color 0.15s;
  }

  textarea:focus, input[type="text"]:focus {
    border-color: var(--info);
    box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
  }

  /* ── SQL / code blocks ──────────────────────── */
  .sql-block {
    font-family: var(--mono);
    font-size: 12px;
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 12px 14px;
    border-radius: var(--radius-sm);
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;
  }

  /* ── Misc utilities ─────────────────────────── */
  .mono { font-family: var(--mono); }
  .text-sm { font-size: 12px; }
  .text-xs { font-size: 11px; }
  .text-secondary { color: var(--text-secondary); }
  .text-danger { color: var(--danger); }
  .text-warning { color: var(--warning); }
  .text-success { color: var(--auto); }
  .font-semibold { font-weight: 600; }
  .font-bold { font-weight: 700; }

  .divider {
    height: 1px;
    background: var(--border);
    margin: 16px 0;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 56px 24px;
    color: var(--text-tertiary);
    text-align: center;
    gap: 8px;
  }

  .empty-state-icon { font-size: 32px; }
  .empty-state-title { font-size: 15px; font-weight: 600; color: var(--text-secondary); }
  .empty-state-desc { font-size: 13px; }

  .spinner {
    display: inline-block;
    width: 16px; height: 16px;
    border: 2px solid var(--border);
    border-top-color: var(--text-secondary);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .loading-center {
    display: flex; align-items: center; justify-content: center;
    gap: 10px; padding: 48px; color: var(--text-secondary);
    font-size: 13px;
  }

  .error-banner {
    background: var(--full-bg);
    border: 1px solid #FECACA;
    border-radius: var(--radius-sm);
    padding: 10px 14px;
    font-size: 13px;
    color: var(--danger);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* ── Countdown timer ─────────────────────── */
  .countdown {
    font-variant-numeric: tabular-nums;
    font-family: var(--mono);
    font-size: 12px;
  }
  .countdown-urgent { color: var(--danger); font-weight: 600; }
  .countdown-warn { color: var(--warning); }
  .countdown-ok { color: var(--text-secondary); }
`

function RiskTierBadge({ tier }) {
  const map = {
    AUTO_EXECUTE: { cls: 'badge-auto', label: 'Auto' },
    STANDARD_REVIEW: { cls: 'badge-standard', label: 'Standard' },
    FULL_CONTRACT: { cls: 'badge-full', label: 'Full Review' },
    UNKNOWN: { cls: 'badge-unknown', label: 'Unknown' },
  }
  const { cls, label } = map[tier] || map.UNKNOWN
  return <span className={`badge ${cls}`}>{label}</span>
}

function OutcomeBadge({ outcome }) {
  const map = {
    APPROVED_EXECUTED: { cls: 'badge-approved', label: 'Approved' },
    APPROVED_EXECUTION_FAILED: { cls: 'badge-rejected', label: 'Exec Failed' },
    REJECTED: { cls: 'badge-rejected', label: 'Rejected' },
    AUTO_REJECTED: { cls: 'badge-rejected', label: 'Auto-Rejected' },
    ROLLED_BACK: { cls: 'badge-rolled-back', label: 'Rolled Back' },
    MODIFIED: { cls: 'badge-modified', label: 'Modified' },
    TIMED_OUT: { cls: 'badge-rejected', label: 'Timed Out' },
    AUTO_EXECUTED: { cls: 'badge-auto-executed', label: 'Auto-Executed' },
    UNKNOWN: { cls: 'badge-unknown', label: 'Unknown' },
  }
  const { cls, label } = map[outcome] || map.UNKNOWN
  return <span className={`badge ${cls}`}>{label}</span>
}

export { RiskTierBadge, OutcomeBadge }

export default function App() {
  return (
    <>
      <style>{styles}</style>
      <BrowserRouter>
        <div className="app-shell">
          <nav className="sidebar">
            <div className="sidebar-brand">
              <div className="sidebar-brand-name">ContraGate</div>
              <div className="sidebar-brand-tagline">Know the consequences before execution.</div>
            </div>
            <div className="sidebar-nav">
              <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
                <span className="nav-icon">⏳</span> Review Queue
              </NavLink>
              <NavLink to="/audit" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
                <span className="nav-icon">📋</span> Audit Log
              </NavLink>
            </div>
            <div className="sidebar-footer">v0.2.0 · MVP</div>
          </nav>
          <main className="main-content">
            <Routes>
              <Route path="/" element={<ApprovalQueue />} />
              <Route path="/approval/:id" element={<ContractView />} />
              <Route path="/audit" element={<AuditLog />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </>
  )
}
