/**
 * What happens when a session expires while someone is working.
 *
 * The startup case is already handled: AuthProvider asks `/auth/me`, gets a 401 and
 * knows to send the browser to Keycloak. The case these tests cover is the one that
 * actually happens to a person - they leave the dashboard open over lunch, come back,
 * click something, and the BFF answers 401 to a request nobody told the auth layer
 * about. Without this, the page renders "Session expired" as an error message and the
 * user is stuck on a screen with no way forward.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './client'
import { onSessionExpired } from './session'

function respondWith(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: '',
    json: async () => body,
  } as Response)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('session expiry', () => {
  it('announces a 401 from any request, not just the identity call', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith(401, { detail: 'Session expired', login_url: '/auth/login' }),
    )
    const listener = vi.fn()
    onSessionExpired(listener)

    await expect(api.get('/api/dashboard')).rejects.toThrow()

    expect(listener).toHaveBeenCalledWith('/auth/login')
  })

  it('passes on the login_url the BFF chose rather than a hard-coded one', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith(401, { detail: 'Session expired', login_url: 'https://bff.acme/auth/login' }),
    )
    const listener = vi.fn()
    onSessionExpired(listener)

    await expect(api.get('/api/employees')).rejects.toThrow()

    expect(listener).toHaveBeenCalledWith('https://bff.acme/auth/login')
  })

  it('stays quiet on a 403, which is a role problem and not a session problem', async () => {
    // Bouncing an HR Manager to Keycloak because they tried an admin-only action
    // would log them out for doing nothing wrong, and they would come back to the
    // same refusal.
    vi.stubGlobal('fetch', respondWith(403, { detail: 'You do not have permission.' }))
    const listener = vi.fn()
    onSessionExpired(listener)

    await expect(api.get('/api/fx-rates')).rejects.toThrow()

    expect(listener).not.toHaveBeenCalled()
  })

  it('stays quiet when the server is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    const listener = vi.fn()
    onSessionExpired(listener)

    await expect(api.get('/api/dashboard')).rejects.toThrow()

    expect(listener).not.toHaveBeenCalled()
  })

  it('stops announcing once a listener unsubscribes', async () => {
    vi.stubGlobal('fetch', respondWith(401, { detail: 'Session expired' }))
    const listener = vi.fn()
    const unsubscribe = onSessionExpired(listener)
    unsubscribe()

    await expect(api.get('/api/dashboard')).rejects.toThrow()

    expect(listener).not.toHaveBeenCalled()
  })

  it('falls back to the BFF login path when the body carries no login_url', async () => {
    vi.stubGlobal('fetch', respondWith(401, { detail: 'Session expired' }))
    const listener = vi.fn()
    onSessionExpired(listener)

    await expect(api.get('/api/dashboard')).rejects.toThrow()

    expect(listener).toHaveBeenCalledWith('/auth/login')
  })
})
