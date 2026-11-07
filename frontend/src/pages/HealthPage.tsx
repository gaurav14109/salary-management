import { useEffect, useState } from 'react'

import { ApiError, api } from '../api/client'

type Health = { status: string }
type State = { kind: 'checking' } | { kind: 'ok'; status: string } | { kind: 'error'; message: string }

/**
 * Proves the round trip - browser to API to Postgres - before any feature exists.
 * Replaced by the real landing page once there is something to land on.
 */
export function HealthPage() {
  const [state, setState] = useState<State>({ kind: 'checking' })

  useEffect(() => {
    let cancelled = false

    api
      .get<Health>('/health')
      .then((health) => {
        if (!cancelled) setState({ kind: 'ok', status: health.status })
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ kind: 'error', message: error.message })
      })

    // React runs effects twice in StrictMode. Without this the second response
    // can land after unmount and warn about setting state on a dead component.
    return () => {
      cancelled = true
    }
  }, [])

  if (state.kind === 'checking') return <p>Checking the backend…</p>
  if (state.kind === 'error') return <p role="alert">{state.message}</p>

  return <p>Backend reachable — status: {state.status}</p>
}
