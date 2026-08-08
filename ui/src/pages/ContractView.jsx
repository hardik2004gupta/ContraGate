import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { RiskTierBadge } from '../App.jsx'
import ContractSection from '../components/ContractSection.jsx'
import DecisionPanel from '../components/DecisionPanel.jsx'
import HistoricalCard from '../components/HistoricalCard.jsx'
import SystemFlags from '../components/SystemFlags.jsx'
import { useApprovalPolling } from '../hooks/useApprovalPolling.js'

const contractStyles = `
  .contract-layout {
    display: grid;
    grid-template-columns: 1fr 300px;
    gap: 20px;
    align-items: start;
  }

  .contract-sections { display: flex; flex-direction: column; gap: 14px; }

  .contract-sidebar {
    position: sticky;
    top: 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .contract-header-bar {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 0;
  }

  .contract-header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  .contract-header-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .contract-op-id { font-family: var(--mono); font-size: 11px; color: var(--text-tertiary); }
  .contract-intent { font-size: 15px; font-weight: 600; color: var(--text-primary); line-height: 1.4; }

  .impact-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .impact-table th {
    text-align: left; font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text-tertiary); padding: 6px 8px;
    border-bottom: 1px solid var(--border);
  }
  .impact-table td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .impact-table tr:last-child td { border-bottom: none; }
  .impact-table .primary-row td { font-weight: 600; }

  .cascade-type {
    font-size: 11px; padding: 1px 6px; border-radius: 3px;
    background: var(--surface-2); color: var(--text-secondary); font-family: var(--mono);
  }

  .total-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 8px; background: var(--surface-2);
    border-radius: var(--radius-sm); margin-top: 10px;
    font-size: 13px; font-weight: 700;
  }
  .total-row-count { font-family: var(--mono); font-size: 15px; color: var(--text-primary); }

  .reversibility-card { border-radius: var(--radius-sm); padding: 12px 14px; font-size: 13px; }
  .rev-permanent { background: var(--permanent-bg); border: 1px solid #C4B5FD; }
  .rev-partial { background: var(--standard-bg); border: 1px solid #FDE68A; }
  .rev-reversible { background: var(--approved-bg); border: 1px solid #6EE7B7; }
  .rev-label { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
  .rev-permanent .rev-label { color: var(--permanent); }
  .rev-partial .rev-label { color: var(--warning); }
  .rev-reversible .rev-label { color: var(--auto); }

  .external-trigger-item {
    display: flex; align-items: flex-start; gap: 8px;
    padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px;
  }
  .external-trigger-item:last-child { border-bottom: none; }
  .external-trigger-badge {
    background: #FEF3C7; border: 1px solid #FDE68A; border-radius: 3px;
    padding: 1px 6px; font-size: 11px; font-weight: 600;
    color: #78350F; flex-shrink: 0; margin-top: 1px;
  }

  .back-link {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 12px; color: var(--text-secondary); cursor: pointer;
    background: none; border: none; padding: 0; font-family: var(--font);
    margin-bottom: 12px;
  }
  .back-link:hover { color: var(--text-primary); }

  .status-banner {
    padding: 12px 14px; border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 600;
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 20px;
  }
  .status-banner.approved { background: var(--approved-bg); color: #065F46; }
  .status-banner.rejected { background: var(--rejected-bg); color: #7F1D1D; }

  @media (max-width: 900px) {
    .contract-layout { grid-template-columns: 1fr; }
    .contract-sidebar { position: static; }
  }
`

function reversilityClass(rev) {
  if (rev === 'PERMANENT') return 'rev-permanent'
  if (rev === 'PARTIAL') return 'rev-partial'
  return 'rev-reversible'
}

function reversilityEmoji(rev) {
  if (rev === 'PERMANENT') return '🔴'
  if (rev === 'PARTIAL') return '🟡'
  return '🟢'
}

function formatNumber(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString()
}

function SectionImpact({ contract }) {
  const cascade = contract.cascade || []
  const primaryRows = contract.estimated_primary_rows ?? 0
  const actualRows = contract.actual_primary_rows ?? null
  const totalRows = contract.blast_radius?.total_rows ??
    (primaryRows + cascade.reduce((s, c) => s + (c.estimated_rows ?? 0), 0))
  const externalTriggers = contract.blast_radius?.external_triggers || []

  return (
    <div>
      <div className="sql-block" style={{ marginBottom: 14 }}>{contract.raw_sql}</div>
      <table className="impact-table">
        <thead>
          <tr>
            <th>Table</th><th>Estimated</th><th>Actual (simulation)</th><th>Type</th>
          </tr>
        </thead>
        <tbody>
          <tr className="primary-row">
            <td>{contract.primary_table || '—'}</td>
            <td className="mono">{formatNumber(primaryRows)}</td>
            <td className="mono">{actualRows != null ? formatNumber(actualRows) : '—'}</td>
            <td><span className="cascade-type">PRIMARY</span></td>
          </tr>
          {cascade.map((c, i) => (
            <tr key={i}>
              <td>{c.table}</td>
              <td className="mono">{formatNumber(c.estimated_rows)}</td>
              <td className="mono">{c.actual_rows != null ? formatNumber(c.actual_rows) : '—'}</td>
              <td><span className="cascade-type">{c.cascade_type || 'CASCADE'}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="total-row">
        <span>Total rows affected across all tables</span>
        <span className="total-row-count">{formatNumber(totalRows)}</span>
      </div>
      {externalTriggers.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="text-sm font-semibold" style={{ marginBottom: 8, color: 'var(--text-secondary)' }}>
            External actions that will fire ({externalTriggers.length}):
          </div>
          {externalTriggers.map((t, i) => (
            <div key={i} className="external-trigger-item">
              <span className="external-trigger-badge">TRIGGER</span>
              <span>{t.trigger_name || t.name || JSON.stringify(t)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SectionReversibility({ contract }) {
  const rev = contract.reversibility
  const revReason = contract.reversibility_reason
  const rollback = contract.rollback_plan

  return (
    <div>
      <div className={`reversibility-card ${reversilityClass(rev)}`}>
        <div className="rev-label">{reversilityEmoji(rev)} {rev || 'UNKNOWN'}</div>
        {revReason && <div style={{ marginTop: 4, lineHeight: 1.5 }}>{revReason}</div>}
      </div>
      {rollback && (
        <div style={{ marginTop: 12 }}>
          <div className="text-sm font-semibold text-secondary" style={{ marginBottom: 6 }}>Recovery procedure:</div>
          <div className="sql-block">{rollback}</div>
        </div>
      )}
      {contract.permanent_components?.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="text-sm font-semibold text-secondary" style={{ marginBottom: 6 }}>Permanent components:</div>
          {contract.permanent_components.map((p, i) => (
            <div key={i} style={{
              padding: '8px 12px', marginBottom: 6, background: 'var(--full-bg)',
              border: '1px solid #FECACA', borderRadius: 'var(--radius-sm)',
              fontSize: 13, color: '#7F1D1D'
            }}>
              {p}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const TERMINAL = new Set(['APPROVED', 'REJECTED', 'AUTO_REJECTED', 'AUTO_EXECUTED', 'COMPLETED', 'FAILED', 'TIMED_OUT'])

export default function ContractView() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [contractData, setContractData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)

  const { status } = useApprovalPolling(id)

  const fetchContract = useCallback(async () => {
    try {
      const res = await fetch(`/v1/approvals/${id}/contract`)
      if (!res.ok) throw new Error(res.status === 404 ? 'Contract not found' : `Server error ${res.status}`)
      setContractData(await res.json())
      setFetchError(null)
    } catch (err) {
      setFetchError(err.message)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { fetchContract() }, [fetchContract])
  useEffect(() => { if (status) fetchContract() }, [status, fetchContract])

  if (loading) return (
    <>
      <style>{contractStyles}</style>
      <div className="page-header"><div className="page-title">Loading…</div></div>
      <div className="page-body"><div className="loading-center"><div className="spinner" /> Loading contract…</div></div>
    </>
  )

  if (fetchError) return (
    <>
      <style>{contractStyles}</style>
      <div className="page-header">
        <button className="back-link" onClick={() => navigate('/')}>← Review Queue</button>
        <div className="page-title">Error</div>
      </div>
      <div className="page-body"><div className="error-banner">⚠ {fetchError}</div></div>
    </>
  )

  const { contract, status: wsStatus } = contractData || {}
  if (!contract) return null

  const isPermanent = contract.reversibility === 'PERMANENT'
  const currentStatus = status || wsStatus
  const terminal = TERMINAL.has(currentStatus)
  const isPositive = ['APPROVED', 'AUTO_EXECUTED', 'COMPLETED'].includes(currentStatus)

  return (
    <>
      <style>{contractStyles}</style>
      <div className="page-header">
        <button className="back-link" onClick={() => navigate('/')}>← Review Queue</button>
        <div className="contract-header-bar">
          <div className="contract-header-top">
            <div className="contract-header-badges">
              <RiskTierBadge tier={contract.risk_tier} />
              {isPermanent && <span className="badge badge-permanent">⚠ PERMANENT</span>}
              {contract.prompt_injection_risk && <span className="badge badge-rejected">🚨 Injection Risk</span>}
            </div>
            <span className="contract-op-id">{contract.operation_id}</span>
          </div>
          <div className="contract-intent">
            {contract.intent_summary || contract.raw_sql?.slice(0, 150)}
          </div>
        </div>
      </div>

      <div className="page-body">
        {terminal && (
          <div className={`status-banner ${isPositive ? 'approved' : 'rejected'}`}>
            {isPositive ? '✅' : '❌'} Decision recorded: <strong>{currentStatus}</strong>
            {contract.decision_reason ? ` — "${contract.decision_reason}"` : ''}
          </div>
        )}

        <div className="contract-layout">
          <div className="contract-sections">
            <ContractSection number="1" question="What will happen?">
              <SectionImpact contract={contract} />
            </ContractSection>

            <ContractSection number="2" question="What cannot be undone?">
              <SectionReversibility contract={contract} />
            </ContractSection>

            <ContractSection number="3" question="Has this happened before?">
              <HistoricalCard
                precedents={contract.historical_precedents || []}
                retrievalAvailable={contract.retrieval_results?.retrieval_available !== false}
              />
            </ContractSection>

            <ContractSection number="4" question="What does the system flag?">
              <SystemFlags contract={contract} />
            </ContractSection>
          </div>

          <div className="contract-sidebar">
            <DecisionPanel
              approvalId={id}
              isPermanent={isPermanent}
              isTerminal={terminal}
              terminalStatus={currentStatus}
              onDecisionSent={fetchContract}
            />
          </div>
        </div>
      </div>
    </>
  )
}
