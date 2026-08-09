import React from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './styles/tokens.css'
import './styles/global.css'
import ApprovalQueue from './pages/ApprovalQueue.jsx'
import ContractView from './pages/ContractView.jsx'
import AuditLog from './pages/AuditLog.jsx'
import Sidebar from './components/layout/Sidebar.jsx'
import { RiskTierBadge as _RiskTierBadge, OutcomeBadge as _OutcomeBadge } from './components/shared/Badge.jsx'

// Re-export for any remaining imports from App.jsx
export const RiskTierBadge = _RiskTierBadge
export const OutcomeBadge = _OutcomeBadge

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Sidebar />
        <main className="main-area">
          <Routes>
            <Route path="/" element={<ApprovalQueue />} />
            <Route path="/approval/:id" element={<ContractView />} />
            <Route path="/audit" element={<AuditLog />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
