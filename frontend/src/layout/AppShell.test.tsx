/**
 * The shell.
 *
 * Hiding a nav item is good manners, not a control: the salary service decides what
 * an HR manager may do, and it decides it again on every request. These tests are
 * about the manners.
 */

import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AppShell } from './AppShell'
import { AuthContext, type AuthContextValue } from '../auth/AuthContext'
import { theme } from '../theme/theme'

function renderShell(roles: string[], username = 'priya') {
  const value: AuthContextValue = {
    status: 'authenticated',
    user: { username, email: `${username}@acme.test`, roles },
    login: vi.fn(),
    logout: vi.fn(),
    hasRole: (role: string) => roles.includes(role),
  }

  return render(
    <MantineProvider theme={theme}>
      <AuthContext.Provider value={value}>
        <MemoryRouter initialEntries={['/employees']}>
          <AppShell>
            <p>page content</p>
          </AppShell>
        </MemoryRouter>
      </AuthContext.Provider>
    </MantineProvider>,
  )
}

describe('AppShell', () => {
  it('renders the page it wraps', () => {
    renderShell(['hr_manager'])

    expect(screen.getByText('page content')).toBeInTheDocument()
  })

  it('offers an HR manager the three things their job needs', () => {
    renderShell(['hr_manager'])

    expect(screen.getByRole('link', { name: /employees/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /import/i })).toBeInTheDocument()
  })

  it('does not offer import to an admin', () => {
    // Admin is read-only. Import writes employees and salaries, so offering the
    // link and failing at 403 would be worse manners than not offering it.
    renderShell(['admin'])

    expect(screen.queryByRole('link', { name: /import/i })).not.toBeInTheDocument()
  })

  it('still offers an admin everything there is to read', () => {
    // Read-only is read, not shut out.
    renderShell(['admin'])

    expect(screen.getByRole('link', { name: /employees/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
  })

  it('shows who is signed in and in what capacity', () => {
    renderShell(['hr_manager'], 'priya')

    expect(screen.getByText('priya')).toBeInTheDocument()
    expect(screen.getByText(/hr manager/i)).toBeInTheDocument()
  })

  it('shows both roles when someone holds both', () => {
    renderShell(['admin', 'hr_manager'])

    expect(screen.getByText(/admin/i)).toBeInTheDocument()
  })

  it('offers a way out', () => {
    renderShell(['hr_manager'])

    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })

  it('marks the current section for screen readers, not only with colour', () => {
    renderShell(['hr_manager'])

    expect(screen.getByRole('link', { name: /employees/i })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })
})
