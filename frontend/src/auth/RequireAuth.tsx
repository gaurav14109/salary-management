import { useEffect, type ReactNode } from 'react'

import { useAuth } from './useAuth'

/**
 * Gate every page behind a session.
 *
 * This is a convenience, not a control. It decides what to render; the BFF decides
 * what is authenticated and the salary service decides what is permitted. A UI that
 * hides what you cannot do is good manners - a UI that is the only thing stopping you
 * is a vulnerability.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth()

  useEffect(() => {
    if (auth.status === 'anonymous') auth.login()
  }, [auth])

  if (auth.status === 'checking') return <p>Checking your session…</p>

  if (auth.status === 'unreachable') {
    return (
      <div role="alert">
        <p>{auth.message}</p>
        <button onClick={() => window.location.reload()}>Try again</button>
      </div>
    )
  }

  // Briefly visible while the redirect to Keycloak is in flight.
  if (auth.status === 'anonymous') return <p>Redirecting you to sign in…</p>

  return <>{children}</>
}
