import { Navigate } from 'react-router-dom'
import type { ReactNode } from 'react'

import { useAuth } from './useAuth'

/**
 * Gate a route on a role, not just on a session.
 *
 * Hiding the nav link is enough until somebody types the URL, follows an old
 * bookmark, or is sent one by a colleague who does have the role. Then the page
 * renders, and every action on it fails at 403 - which reads as a broken product
 * rather than as a permission they do not have.
 *
 * Like RequireAuth, this is manners rather than control: the salary service decides
 * what is permitted, and it refuses the same request whatever this component does.
 * What this adds is that the refusal arrives before the work, not after it.
 *
 * Redirects rather than showing "not allowed". Someone who cannot import has not
 * done anything wrong - they have followed a link meant for somebody else, and the
 * useful response is to put them somewhere they can work.
 */
export function RequireRole({ role, children }: { role: string; children: ReactNode }) {
  const auth = useAuth()

  if (!auth.hasRole(role)) return <Navigate to="/employees" replace />

  return <>{children}</>
}
