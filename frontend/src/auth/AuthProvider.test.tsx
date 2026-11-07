import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './useAuth'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, get: vi.fn(), post: vi.fn() } }
})

const { api } = await import('../api/client')
const get = vi.mocked(api.get)

function Probe() {
  const auth = useAuth()
  return (
    <div>
      <span data-testid="status">{auth.status}</span>
      {auth.status === 'authenticated' && <span data-testid="user">{auth.user.username}</span>}
      {auth.status === 'unreachable' && <span data-testid="message">{auth.message}</span>}
      <button onClick={auth.login}>sign in</button>
    </div>
  )
}

function renderAuth() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

afterEach(() => vi.resetAllMocks())

describe('AuthProvider', () => {
  it('reports the signed-in user', async () => {
    get.mockResolvedValue({ authenticated: true, username: 'hr', email: 'hr@acme.test', roles: ['hr_manager'] })

    renderAuth()

    expect(await screen.findByTestId('user')).toHaveTextContent('hr')
  })

  it('treats a 401 as anonymous, not as an error', async () => {
    get.mockRejectedValue(new ApiError(401, 'Not authenticated', {}, '/auth/login'))

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
  })

  it('does not tell the user to sign in when the server is unreachable', async () => {
    /* Sending someone to re-authenticate because the backend is down puts them in a
       loop and hides the real problem. */
    get.mockRejectedValue(new ApiError(0, 'Could not reach the server'))

    renderAuth()

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unreachable'))
    expect(screen.getByTestId('message')).toHaveTextContent('Could not reach the server')
  })

  it('navigates the whole window to the login url, carrying where to come back to', async () => {
    /* A whole-window navigation, not a fetch: the browser has to follow the redirect
       to Keycloak and return with a cookie. */
    const assign = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { assign, pathname: '/employees', search: '?country=IN' },
      writable: true,
    })
    get.mockRejectedValue(new ApiError(401, 'Not authenticated', {}, '/auth/login'))

    renderAuth()
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('anonymous'))
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(assign).toHaveBeenCalledWith(
      '/auth/login?return_to=' + encodeURIComponent('/employees?country=IN'),
    )
  })
})
