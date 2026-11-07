import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { ApiError, api } from '../api/client'
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
    const returnTo = window.location.pathname + window.location.search
    const loginUrl = state.status === 'anonymous' ? state.loginUrl : '/auth/login'
    // A whole-window navigation, not a fetch: the browser has to follow the
    // redirect to Keycloak and come back with a cookie.
    window.location.assign(`${loginUrl}?return_to=${encodeURIComponent(returnTo)}`)
  }, [state])

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
