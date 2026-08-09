import React, { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'

export default function Sidebar() {
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/v1/approvals')
        if (r.ok) {
          const d = await r.json()
          setPendingCount((d.approvals || []).length)
        }
      } catch (_) {}
    }
    poll()
    const t = setInterval(poll, 10000)
    return () => clearInterval(t)
  }, [])

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-mark">
          <div className="sidebar-brand-icon">⬡</div>
          <span className="sidebar-brand-name">CONTRAGATE</span>
        </div>
        <div className="sidebar-brand-tagline">Consequence-aware execution gate</div>
      </div>

      <div className="sidebar-nav">
        <div className="nav-section-label">Review</div>

        <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <div className="nav-link-left">
            <span className="nav-icon">⏳</span>
            <span>Approvals</span>
          </div>
          {pendingCount > 0 && (
            <span className="nav-badge">{pendingCount > 99 ? '99+' : pendingCount}</span>
          )}
        </NavLink>

        <NavLink to="/audit" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <div className="nav-link-left">
            <span className="nav-icon">📋</span>
            <span>Audit Log</span>
          </div>
        </NavLink>

        <div className="nav-separator" />
        <div className="nav-section-label">System</div>

        <div className="nav-link" style={{ cursor: 'default', opacity: 0.6 }}>
          <div className="nav-link-left">
            <span className="nav-icon">⚡</span>
            <span>Pipeline</span>
          </div>
        </div>

        <div className="nav-link" style={{ cursor: 'default', opacity: 0.6 }}>
          <div className="nav-link-left">
            <span className="nav-icon">🛡</span>
            <span>Policies</span>
          </div>
        </div>
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-status-row">
          <div className="status-dot" />
          <span>System operational</span>
        </div>
        <div className="sidebar-version">v0.2.0 · MVP · Local</div>
      </div>
    </nav>
  )
}
