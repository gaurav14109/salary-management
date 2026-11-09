/**
 * Hiding a link is not the same as closing a route.
 *
 * The nav stopped offering Import to an admin, but the route was still there: a
 * typed URL, an old bookmark or a link from a colleague rendered the page, and
 * every upload on it failed at 403. That reads as a broken product rather than as
 * a permission the user does not have.
 */

import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { RequireRole } from './RequireRole'
import { renderPage } from '../test/render'

function render(roles: string[]) {
  return renderPage(
    <Routes>
      <Route
        path="/import"
        element={
          <RequireRole role="hr_manager">
            <p>the import page</p>
          </RequireRole>
        }
      />
      <Route path="/employees" element={<p>the employee list</p>} />
    </Routes>,
    { roles, route: '/import' },
  )
}

describe('RequireRole', () => {
  it('lets the role through', () => {
    render(['hr_manager'])

    expect(screen.getByText('the import page')).toBeInTheDocument()
  })

  it('does not render the page for a role without it', () => {
    render(['admin'])

    expect(screen.queryByText('the import page')).not.toBeInTheDocument()
  })

  it('sends them somewhere they can work rather than showing a refusal', () => {
    // Following a link meant for somebody else is not an error on their part.
    render(['admin'])

    expect(screen.getByText('the employee list')).toBeInTheDocument()
  })

  it('lets someone holding both roles through', () => {
    render(['admin', 'hr_manager'])

    expect(screen.getByText('the import page')).toBeInTheDocument()
  })
})
