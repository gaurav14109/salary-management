import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { ApiError, api } from '../api/client'
import { onSessionExpired } from '../api/session'
import { AuthContext, type AuthState } from './AuthContext'

type MeResponse = { authenticated: true; username: string; email: string | null; roles: string[] }

/**
 * Who is logged in, asked once at startup.
 *
 * The SPA knows nothing about OIDC: no client id, no PKCE, no authorization URL. It
 * asks the BFF who the user is, and on 401 it is told where to go. Swapping identity
 * provider changes nothing in this file.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'checking' })

  const check = useCallback(async () => {
    try {
      const me = await api.get<MeResponse>('/auth/me')
      setState({
        status: 'authenticated',
        user: { username: me.username, email: me.email, roles: me.roles },
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setState({ status: 'anonymous', loginUrl: error.loginUrl ?? '/auth/login' })
        return
      }
      // A failure that is not a 401 is not "log in again" - telling someone to
      // re-authenticate when the server is down sends them round a loop.
      setState({
        status: 'unreachable',
        message: error instanceof ApiError ? error.message : 'Could not reach the server',
      })
    }
  }, [])

  useEffect(() => {
    // Asking the BFF who is logged in: synchronising with an external system, which
    // is exactly what an effect is for. react/set-state-in-effect is off in
    // .oxlintrc.json for this reason - it assumes state set in an effect is state
    // that should have been derived during render, and identity cannot be derived
    // without a network call.
    void check()
  }, [check])

  const login = useCallback(() => {
    const loginUrl = state.status === 'anonymous' ? state.loginUrl : '/auth/login'
    redirectToLogin(loginUrl)
  }, [state])

  // Already navigating. A dashboard fires several queries at once, so one expired
  // session produces several 401s and several announcements; without this they
  // become several navigations racing each other.
  const redirecting = useRef(false)

  useEffect(
    () =>
      onSessionExpired((loginUrl) => {
        if (redirecting.current) return
        redirecting.current = true
        // Recorded as well as acted on: the redirect is a whole-page navigation that
        // takes a moment, and in that moment the tree should render "redirecting"
        // rather than the stale authenticated view.
        setState({ status: 'anonymous', loginUrl })
        redirectToLogin(loginUrl)
      }),
    [],
  )

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout')
    } finally {
      await check()
    }
  }, [check])

  const hasRole = useCallback(
    (role: string) => state.status === 'authenticated' && state.user.roles.includes(role),
    [state],
  )

  return (
    <AuthContext.Provider value={{ ...state, login, logout, hasRole }}>
      {children}
    </AuthContext.Provider>
  )
}

/**
 * A whole-window navigation, not a fetch: the browser has to follow the redirect to
 * Keycloak and come back with a cookie. `return_to` is where the user was, so signing
 * in again puts them back on the page they were reading rather than on the dashboard.
 */
function redirectToLogin(loginUrl: string): void {
  const returnTo = window.location.pathname + window.location.search
  window.location.assign(`${loginUrl}?return_to=${encodeURIComponent(returnTo)}`)
}
