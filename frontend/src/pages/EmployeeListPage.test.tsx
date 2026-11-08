/**
 * The employee list - where an HR manager lands and where the work starts.
 *
 * Two properties carry most of these tests: the server is the source of truth for
 * sorting and paging (the table never reorders a partial page by itself, which would
 * show a confidently wrong "highest paid"), and someone with no salary row yet is
 * surfaced rather than hidden.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmployeeListPage } from './EmployeeListPage'
import * as resources from '../api/resources'
import { aListPage, aSalary, anEmployee, referenceData } from '../test/fixtures'
import { renderPage } from '../test/render'

function listSpy(page = aListPage()) {
  return vi.spyOn(resources.employees, 'list').mockResolvedValue(page)
}

beforeEach(() => {
  vi.spyOn(resources.reference, 'all').mockResolvedValue(referenceData())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('EmployeeListPage', () => {
  it('renders a page of employees', async () => {
    listSpy()
    renderPage(<EmployeeListPage />)

    expect(await screen.findByText('Priya Sharma')).toBeInTheDocument()
    expect(screen.getByText('EMP-1042')).toBeInTheDocument()
  })

  it('shows the USD figure with the local amount beneath it', async () => {
    // USD is the column you can compare across countries; the local amount is what
    // the person is actually paid. Showing only one of them is a different lie each
    // way round.
    listSpy()
    renderPage(<EmployeeListPage />)

    expect(await screen.findByText('$14,379.87')).toBeInTheDocument()
    expect(screen.getByText(/1,200,000/)).toBeInTheDocument()
  })

  it('surfaces someone with no salary as a job to do, not a blank cell', async () => {
    listSpy(aListPage([{ employee: anEmployee({ employee_code: 'EMP-NEW' }), current_salary: null }]))
    renderPage(<EmployeeListPage />)

    expect(await screen.findByText(/set salary/i)).toBeInTheDocument()
  })

  it('debounces typing rather than firing a request per keystroke', async () => {
    const list = listSpy()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderPage(<EmployeeListPage />)
    await waitFor(() => expect(list).toHaveBeenCalled())
    list.mockClear()

    await user.type(screen.getByRole('searchbox'), 'sharma')
    expect(list).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(400)
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    expect(list.mock.calls[0][0]).toMatchObject({ q: 'sharma' })

    vi.useRealTimers()
  })

  it('returns to page one when a filter changes', async () => {
    // Otherwise filtering to 3 results while on page 4 shows an empty table, and the
    // user reads it as "no matches" rather than "wrong page".
    const list = listSpy(aListPage([{ employee: anEmployee(), current_salary: aSalary() }], {
      total: 200,
    }))
    const user = userEvent.setup()
    renderPage(<EmployeeListPage />)
    await screen.findByText('Priya Sharma')

    await user.click(screen.getByRole('button', { name: /next page/i }))
    await waitFor(() => expect(list.mock.calls.at(-1)?.[0]).toMatchObject({ page: 2 }))

    // Wait for the reference data before opening the dropdown: clicking a Select
    // whose options have not arrived opens an empty list, and it does not reopen
    // when they do.
    await waitFor(() => expect(resources.reference.all).toHaveBeenCalled())
    // Mantine puts the aria-label on the input *and* its listbox, so the role has
    // to be named explicitly - getByLabelText finds both.
    await user.click(screen.getByRole('combobox', { name: 'Country' }))
    await user.click(await screen.findByText('Germany'))

    await waitFor(() =>
      expect(list.mock.calls.at(-1)?.[0]).toMatchObject({ country: 'DE', page: 1 }),
    )
  })

  it('asks the server to re-sort rather than reordering the page it has', async () => {
    // Sorting a 50-row page client-side produces a "highest paid" that is only the
    // highest on that page - confidently wrong, and impossible to spot.
    const list = listSpy()
    const user = userEvent.setup()
    renderPage(<EmployeeListPage />)
    await screen.findByText('Priya Sharma')
    list.mockClear()

    await user.click(screen.getByRole('button', { name: /sort by salary/i }))

    await waitFor(() =>
      expect(list.mock.calls.at(-1)?.[0]).toMatchObject({ sort: 'usd_amount', order: 'desc' }),
    )
  })

  it('toggles the sort direction on a second click', async () => {
    const list = listSpy()
    const user = userEvent.setup()
    renderPage(<EmployeeListPage />)
    await screen.findByText('Priya Sharma')

    const header = screen.getByRole('button', { name: /sort by salary/i })
    await user.click(header)
    await waitFor(() => expect(list.mock.calls.at(-1)?.[0]).toMatchObject({ order: 'desc' }))
    await user.click(header)
    await waitFor(() => expect(list.mock.calls.at(-1)?.[0]).toMatchObject({ order: 'asc' }))
  })

  it('shows an empty state when nothing matches', async () => {
    listSpy(aListPage([], { total: 0 }))
    renderPage(<EmployeeListPage />)

    expect(await screen.findByText(/no employees match/i)).toBeInTheDocument()
  })

  it('tells the user when the server is unreachable instead of showing an empty table', async () => {
    // An empty table after a failed request reads as "no employees", which is a
    // materially different and alarming thing.
    vi.spyOn(resources.employees, 'list').mockRejectedValue(new Error('offline'))
    renderPage(<EmployeeListPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load/i)
  })

  it('offers export to anyone who can read the list', async () => {
    listSpy()
    renderPage(<EmployeeListPage />)

    expect(await screen.findByRole('link', { name: /export/i })).toBeInTheDocument()
  })

  it('carries the active filters into the export link', async () => {
    // An export that ignores the filters is a 10,000-row file when someone asked for
    // one department.
    const list = listSpy()
    const user = userEvent.setup()
    renderPage(<EmployeeListPage />)
    await screen.findByText('Priya Sharma')

    await waitFor(() => expect(resources.reference.all).toHaveBeenCalled())
    await user.click(screen.getByRole('combobox', { name: 'Country' }))
    await user.click(await screen.findByText('Germany'))
    await waitFor(() => expect(list.mock.calls.at(-1)?.[0]).toMatchObject({ country: 'DE' }))

    expect(screen.getByRole('link', { name: /export/i })).toHaveAttribute(
      'href',
      expect.stringContaining('country=DE'),
    )
  })

  it('does not offer an HR manager the button to add an employee', async () => {
    // Creating a person is an admin act. Offering it and failing at 403 is worse
    // manners than not offering it.
    listSpy()
    renderPage(<EmployeeListPage />, { roles: ['hr_manager'] })
    await screen.findByText('Priya Sharma')

    expect(screen.queryByRole('button', { name: /add employee/i })).not.toBeInTheDocument()
  })

  it('offers an admin the button to add an employee', async () => {
    listSpy()
    renderPage(<EmployeeListPage />, { roles: ['admin'] })

    expect(await screen.findByRole('button', { name: /add employee/i })).toBeInTheDocument()
  })

  it('links each row to the person, so the timeline is one click away', async () => {
    listSpy()
    renderPage(<EmployeeListPage />)

    const row = (await screen.findByText('Priya Sharma')).closest('tr')!
    expect(within(row).getByRole('link')).toHaveAttribute('href', '/employees/1')
  })
})
