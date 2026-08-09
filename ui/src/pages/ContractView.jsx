import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { RiskTierBadge, ReversibilityBadge } from '../components/shared/Badge.jsx'
import ContractSection from '../components/contract/ContractSection.jsx'
import OperationSection from '../components/contract/OperationSection.jsx'
import BlastRadiusSection from '../components/contract/BlastRadiusSection.jsx'
import ReversibilitySection from '../components/contract/ReversibilitySection.jsx'
import SandboxSection from '../components/contract/SandboxSection.jsx'
import PrecedentsSection from '../components/contract/PrecedentsSection.jsx'
import FlagsSection from '../components/contract/FlagsSection.jsx'
import DecisionPanel from '../components/decision/DecisionPanel.jsx'
import { ContractSkeleton } from '../components/shared/Skeleton.jsx'
import { useApprovalPolling } from '../hooks/useApprovalPolling.js'

const TERMINAL = new Set(['APPROVED', 'REJECTED', 'AUTO_REJECTED', 'AUTO_EXECUTED', 'COMPLETED', 'FAILED', 'TIMED_OUT'])

const TERMINAL_DISPLAY = {
  APPROVED:      { icon: '✓', label: 'Approved', color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'var(--green-border)' },
  AUTO_EXECUTED: { icon: '⚡', label: 'Auto-Executed', color: 'var(--cyan)',   bg: 'rgba(34,211,238,.08)', border: 'rgba(34,211,238,.25)' },
  COMPLETED:     { icon: '✓', label: 'Completed', color: 'var(--green)',  bg: 'var(--green-dim)',  border: 'var(--green-border)' },
  REJECTED:      { icon: '✕', label: 'Rejected',  color: 'var(--red)',    bg: 'var(--red-dim)',    border: 'var(--red-border)' },
  AUTO_REJECTED: { icon: '🚫', label: 'Auto-Rejected by Policy', color: 'var(--red)', bg: 'var(--red-dim)', border: 'var(--red-border)' },
  TIMED_OUT:     { icon: '⏱', label: 'Timed Out — Auto-Rejected', color: 'var(--red)', bg: 'var(--red-dim)', border: 'var(--red-border)' },
  FAILED:        { icon: '✕', label: 'Failed',    color: 'var(--red)',    bg: 'var(--red-dim)',    border: 'var(--red-border)' },
}

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
      <div className="page-header">
        <button
          onClick={() => navigate('/')}
          style={{ background: 'none', border: 'none', color: 'var(--text-3)', fontSize: '12px', cursor: 'pointer', padding: 0, marginBottom: '8px', fontFamily: 'var(--font)' }}
        >
          ← Review Queue
        </button>
        <div className="page-title">Loading Contract…</div>
      </div>
      <div className="page-body"><ContractSkeleton /></div>
    </>
  )

  if (fetchError) return (
    <>
      <div className="page-header">
        <button
          onClick={() => navigate('/')}
          style={{ background: 'none', border: 'none', color: 'var(--text-3)', fontSize: '12px', cursor: 'pointer', padding: 0, marginBottom: '8px', fontFamily: 'var(--font)' }}
        >
          ← Review Queue
        </button>
        <div className="page-title">Error</div>
      </div>
      <div className="page-body"><div className="error-banner">⚠ {fetchError}</div></div>
    </>
  )

  const { contract, status: wsStatus } = contractData || {}
  if (!contract) return null

  const isPermanent = contract.reversibility === 'PERMANENT'
  const currentStatus = status || wsStatus
  const isTerminal = TERMINAL.has(currentStatus)
  const termMeta = TERMINAL_DISPLAY[currentStatus]

  const flatContract = {
    ...contract,
    reversibility: contract.reversibility_classification || contract.reversibility || contract.reversibility?.classification,
    reversibility_reason: contract.reversibility_reason || contract.reversibility?.reason,
    rollback_plan: contract.rollback_plan || contract.reversibility?.rollback_sql,
    permanent_components: contract.permanent_components || contract.reversibility?.permanent_components || [],
    estimated_primary_rows: contract.estimated_primary_rows ?? contract.parsed_plan?.estimated_primary_rows ?? contract.blast_radius?.primary_rows,
    operation_type: contract.operation_type || contract.parsed_plan?.operation_type,
    primary_table: contract.primary_table || contract.parsed_plan?.primary_table,
    cascade: contract.cascade || contract.blast_radius?.cascade || [],
    historical_precedents: contract.historical_precedents || contract.retrieval_results?.top3_historical || [],
    blast_radius_confidence_reason: contract.blast_radius_confidence_reason,
  }

  return (
    <>
      {/* Page header */}
      <div className="page-header" style={{ paddingBottom: '16px' }}>
        <button
          onClick={() => navigate('/')}
          style={{
            background: 'none', border: 'none', color: 'var(--text-3)',
            fontSize: '12px', cursor: 'pointer', padding: 0, marginBottom: '12px',
            fontFamily: 'var(--font)', display: 'flex', alignItems: 'center', gap: '4px',
            transition: 'color var(--t-1)',
          }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-1)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-3)'}
        >
          ← Review Queue
        </button>

        {/* Contract hero */}
        <div style={{
          background: 'var(--surface-2)',
          border: `1px solid ${isPermanent ? 'var(--purple-border)' : flatContract.risk_tier === 'FULL_CONTRACT' ? 'var(--red-border)' : 'var(--border-1)'}`,
          borderRadius: 'var(--r-lg)',
          padding: '16px 20px',
          boxShadow: isPermanent ? '0 0 24px var(--purple-dim)' : flatContract.risk_tier === 'FULL_CONTRACT' ? '0 0 16px var(--red-dim)' : 'none',
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            flexWrap: 'wrap',
            marginBottom: '8px',
          }}>
            <RiskTierBadge tier={flatContract.risk_tier} />
            {isPermanent && (
              <span style={{
                fontSize: '10px',
                fontWeight: '800',
                color: 'var(--purple)',
                background: 'var(--purple-dim)',
                border: '1px solid var(--purple-border)',
                padding: '2px 8px',
                borderRadius: '100px',
                letterSpacing: '0.4px',
                textTransform: 'uppercase',
              }}>⚠ PERMANENT</span>
            )}
            {flatContract.prompt_injection_risk && (
              <span style={{
                fontSize: '10px',
                fontWeight: '800',
                color: 'var(--red)',
                background: 'var(--red-dim)',
                border: '1px solid var(--red-border)',
                padding: '2px 8px',
                borderRadius: '100px',
                letterSpacing: '0.4px',
                textTransform: 'uppercase',
              }}>🚨 Injection Risk</span>
            )}
            <span style={{
              marginLeft: 'auto',
              fontFamily: 'var(--mono)',
              fontSize: '11px',
              color: 'var(--text-3)',
            }}>{flatContract.operation_id}</span>
          </div>

          <div style={{
            fontSize: '16px',
            fontWeight: '600',
            color: 'var(--text-0)',
            lineHeight: '1.4',
          }}>
            {flatContract.intent_summary || flatContract.raw_sql?.slice(0, 150)}
          </div>
        </div>
      </div>

      <div className="page-body">
        {/* Terminal status banner */}
        {isTerminal && termMeta && (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '12px 16px',
            background: termMeta.bg,
            border: `1px solid ${termMeta.border}`,
            borderRadius: 'var(--r-md)',
            marginBottom: '20px',
            fontSize: '13px',
            fontWeight: '600',
            color: termMeta.color,
          }}>
            <span>{termMeta.icon}</span>
            <span>Decision recorded: <strong>{termMeta.label}</strong></span>
            {flatContract.decision_reason ? (
              <span style={{ color: 'var(--text-2)', fontWeight: '400', fontStyle: 'italic', marginLeft: '4px' }}>
                — "{flatContract.decision_reason}"
              </span>
            ) : null}
          </div>
        )}

        {/* Two-column layout */}
        <div className="contract-two-col" style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 320px',
          gap: '20px',
          alignItems: 'start',
        }}>
          {/* Left: contract sections */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <ContractSection
              number="1"
              question="What will happen?"
              defaultOpen={true}
            >
              <OperationSection contract={flatContract} />
            </ContractSection>

            <ContractSection
              number="2"
              question="What cannot be undone?"
              defaultOpen={true}
              accent={isPermanent ? 'var(--purple)' : undefined}
            >
              <ReversibilitySection contract={flatContract} />
            </ContractSection>

            <ContractSection
              number="2b"
              question="Simulation results"
              defaultOpen={!!flatContract.simulation?.executed}
            >
              <SandboxSection contract={flatContract} />
            </ContractSection>

            <ContractSection
              number="3"
              question="Has this happened before?"
              defaultOpen={false}
            >
              <PrecedentsSection
                precedents={flatContract.historical_precedents}
                retrievalAvailable={flatContract.retrieval_results?.retrieval_available !== false}
              />
            </ContractSection>

            <ContractSection
              number="4"
              question="What does the system flag?"
              defaultOpen={(flatContract.policy_violations?.length > 0) || flatContract.prompt_injection_risk}
              accent={flatContract.policy_violations?.length > 0 ? 'var(--red)' : undefined}
            >
              <FlagsSection contract={flatContract} />
            </ContractSection>
          </div>

          {/* Right: decision sidebar */}
          <div className="contract-sidebar-col" style={{ position: 'sticky', top: '20px' }}>
            {/* Blast radius quick stat */}
            <div style={{
              background: 'var(--surface-1)',
              border: '1px solid var(--border-1)',
              borderRadius: 'var(--r-lg)',
              padding: '14px 16px',
              marginBottom: '12px',
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: '10px',
            }}>
              <div>
                <div style={{ fontSize: '10px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: '3px' }}>
                  Est. Impact
                </div>
                <div style={{ fontSize: '22px', fontWeight: '800', fontFamily: 'var(--mono)', color: 'var(--text-0)', letterSpacing: '-1px' }}>
                  {flatContract.blast_radius?.total_rows != null
                    ? Number(flatContract.blast_radius.total_rows).toLocaleString()
                    : flatContract.estimated_primary_rows != null
                    ? Number(flatContract.estimated_primary_rows).toLocaleString()
                    : '—'}
                </div>
                <div style={{ fontSize: '10px', color: 'var(--text-3)', marginTop: '2px' }}>rows</div>
              </div>
              <div>
                <div style={{ fontSize: '10px', fontWeight: '700', textTransform: 'uppercase', letterSpacing: '0.7px', color: 'var(--text-3)', marginBottom: '3px' }}>
                  Reversibility
                </div>
                <div style={{ marginTop: '4px' }}>
                  <ReversibilityBadge rev={flatContract.reversibility} />
                </div>
              </div>
            </div>

            <DecisionPanel
              approvalId={id}
              isPermanent={isPermanent}
              isTerminal={isTerminal}
              terminalStatus={currentStatus}
              onDecisionSent={fetchContract}
            />
          </div>
        </div>
      </div>

      <style>{`
        @media (max-width: 1100px) {
          .contract-two-col { grid-template-columns: 1fr !important; }
          .contract-sidebar-col { position: static !important; }
        }
      `}</style>
    </>
  )
}
