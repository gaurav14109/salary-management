/**
 * Mount a page the way production mounts it.
 *
 * Every provider the app wraps around a page is here, so a test exercises the real
 * theme, the real query client and the real router rather than a thinner stack that
 * drifts from production and passes for the wrong reason.
 *
 * The session is the one thing faked: tests decide who is signed in, because that is
 * what they are varying.
 */

import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within, type RenderOptions } from '@testing-library/react'
import type { UserEvent } from '@testing-library/user-event'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthContext, type AuthContextValue } from '../auth/AuthContext'
import { cssVariablesResolver, theme } from '../theme/theme'

export function fakeAuth(roles: string[] = ['hr_manager'], username = 'priya'): AuthContextValue {
  return {
    status: 'authenticated',
    user: { username, email: `${username}@acme.test`, roles },
    login: vi.fn(),
    logout: vi.fn(),
    hasRole: (role: string) => roles.includes(role),
  }
}

/** Retries off: a test asserting an error state should not wait out two retries. */
export function testQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  })
}

type Options = RenderOptions & {
  roles?: string[]
  /** The URL being visited. */
  route?: string
  /**
   * The route pattern the page is mounted at, when it reads params.
   *
   * Without this a page calling `useParams()` gets an empty object and renders its
   * loading state forever - which looks like a broken component and is a missing
   * Route.
   */
  path?: string
  client?: QueryClient
}

export function renderPage(ui: ReactElement, options: Options = {}) {
  const {
    roles = ['hr_manager'],
    route = '/',
    path,
    client = testQueryClient(),
    ...rest
  } = options

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver}>
        <Notifications />
        <QueryClientProvider client={client}>
          <AuthContext.Provider value={fakeAuth(roles)}>
            <MemoryRouter initialEntries={[route]}>
              {path ? (
                <Routes>
                  <Route path={path} element={children} />
                </Routes>
              ) : (
                children
              )}
            </MemoryRouter>
          </AuthContext.Provider>
        </QueryClientProvider>
      </MantineProvider>
    )
  }

  return render(ui, { wrapper: Wrapper, ...rest })
}


/**
 * Pick a value from a Mantine `Select`.
 *
 * Three things make this fiddly enough to be worth writing once:
 *
 *  * the same aria-label is on the input *and* on its listbox, so `getByLabelText`
 *    matches two elements;
 *  * the options are rendered but are not reliably exposed under the `option` role
 *    in jsdom, so `getByRole('option')` finds nothing;
 *  * a label like "Germany" also appears on a chart axis and in a table, so a bare
 *    text query picks the wrong node.
 *
 * Matching on Mantine's own `data-combobox-option` marker sidesteps all three, and
 * keeps the quirk in one place instead of in every test that filters something.
 */
export async function selectOption(user: UserEvent, label: string, optionText: string) {
  await user.click(screen.getByRole('combobox', { name: label }))

  const option = await waitFor(() => {
    const candidates = Array.from(
      document.querySelectorAll<HTMLElement>('[data-combobox-option]'),
    ).filter((element) => element.textContent?.trim() === optionText)
    if (candidates.length === 0) throw new Error(`No option "${optionText}" under ${label}`)
    return candidates[candidates.length - 1]
  })

  await user.click(option)
}

export { within }
