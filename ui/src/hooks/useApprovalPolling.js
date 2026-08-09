import { useState, useEffect, useRef, useCallback } from 'react'

const POLL_INTERVAL_MS = 3000
const TERMINAL_STATUSES = new Set([
  'APPROVED', 'REJECTED', 'AUTO_REJECTED', 'AUTO_EXECUTED',
  'COMPLETED', 'FAILED', 'TIMED_OUT',
])

/**
 * useApprovalPolling — SSE subscription with polling fallback.
 *
 * Subscribes to GET /v1/approvals/{id}/stream for real-time updates.
 * Falls back to polling GET /v1/approvals/{id}/status every 3 seconds
 * if SSE fails to connect or is unavailable.
 *
 * Returns { status, result, reason, error, isLoading, isTerminal }
 */
export function useApprovalPolling(approvalId) {
  const [state, setState] = useState({
    status: null,
    result: null,
    reason: null,
    error: null,
    isLoading: true,
    isTerminal: false,
  })

  const esRef = useRef(null)
  const pollTimerRef = useRef(null)
  const mountedRef = useRef(true)

  const cleanup = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const applyUpdate = useCallback((data) => {
    if (!mountedRef.current) return
    const terminal = TERMINAL_STATUSES.has(data.status)
    setState({
      status: data.status,
      result: data.execution_result ?? null,
      reason: data.reason ?? null,
      error: null,
      isLoading: false,
      isTerminal: terminal,
    })
    if (terminal) {
      cleanup()
    }
  }, [cleanup])

  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return

    const poll = async () => {
      if (!mountedRef.current) return
      try {
        const res = await fetch(`/v1/approvals/${approvalId}/status`)
        if (!res.ok) {
          if (res.status === 404 && mountedRef.current) {
            setState(s => ({ ...s, error: 'Approval not found', isLoading: false }))
            cleanup()
          }
          return
        }
        const data = await res.json()
        applyUpdate(data)
      } catch (err) {
        if (mountedRef.current) {
          setState(s => ({ ...s, error: `Poll error: ${err.message}`, isLoading: false }))
        }
      }
    }

    poll()
    pollTimerRef.current = setInterval(poll, POLL_INTERVAL_MS)
  }, [approvalId, applyUpdate, cleanup])

  useEffect(() => {
    if (!approvalId) return
    mountedRef.current = true

    try {
      const es = new EventSource(`/v1/approvals/${approvalId}/stream`)
      esRef.current = es

      es.onmessage = (evt) => {
        try { applyUpdate(JSON.parse(evt.data)) } catch (_) {}
      }

      es.onerror = () => {
        es.close()
        esRef.current = null
        startPolling()
      }
    } catch (_) {
      startPolling()
    }

    return () => {
      mountedRef.current = false
      cleanup()
    }
  }, [approvalId, applyUpdate, startPolling, cleanup])

  return state
}
