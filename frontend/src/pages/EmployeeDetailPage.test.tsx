/**
 * One person: what they are paid now, and how it got that way.
 *
 * The history is a timeline of *decisions*, not a table of date ranges - so each
 * entry says who made it and when it was made, which is not the same as when the pay
 * started applying.
 *
 * Two refusals get most of the attention here: a change that changes nothing must
 * not look like a raise, and a missing FX rate must read as something an admin can
 * fix rather than as a failure.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmployeeDetailPage } from './EmployeeDetailPage'
import * as resources from '../api/resources'
import { ApiError } from '../api/client'
import type { EmployeeDetail } from '../api/types'
import { aSalary, anEmployee, referenceData } from '../test/fixtures'
import { renderPage } from '../test/render'

function aDetail(overrides: Partial<EmployeeDetail> = {}): EmployeeDetail {
  const current = aSalary()
  return {
    employee: anEmployee(),
    current_salary: current,
    history: [current],
    ...overrides,
  }
}

function detailSpy(detail = aDetail()) {
  return vi.spyOn(resources.employees, 'detail').mockResolvedValue(detail)
}

function render(detail = aDetail(), roles = ['hr_manager']) {
  return renderPage(<EmployeeDetailPage />, {
    roles,
    route: `/employees/${detail.employee.id}`,
    path: '/employees/:id',
  })
}

beforeEach(() => {
  vi.spyOn(resources.reference, 'all').mockResolvedValue(referenceData())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('EmployeeDetailPage', () => {
  it('shows who the person is', async () => {
    detailSpy()
    render()

    expect(await screen.findByRole('heading', { name: /priya sharma/i })).toBeInTheDocument()
    expect(screen.getByText(/EMP-1042/)).toBeInTheDocument()
  })

  it('shows the current salary in USD and in what they are actually paid', async () => {
    detailSpy()
    render()

    // Twice over: the card at the top and the entry in the timeline. Both are
    // correct, so the assertion is "at least once" rather than "exactly one".
    expect(await screen.findAllByText('$14,379.87')).not.toHaveLength(0)
    expect(screen.getAllByText(/1,200,000/).length).toBeGreaterThan(0)
  })

  it('shows the rate that produced the USD figure, and its date', async () => {
    // An unexplained converted number is the first thing an HR manager distrusts -
    // and two people on identical local pay can legitimately show different USD
    // amounts, because the rate is frozen per row.
    detailSpy()
    render()

    expect(await screen.findAllByText(/83\.45/)).not.toHaveLength(0)
    expect(screen.getAllByText(/01 Jan 2026/).length).toBeGreaterThan(0)
  })

  it('says plainly when someone has no salary yet', async () => {
    detailSpy(aDetail({ current_salary: null, history: [] }))
    render()

    // Said in the card and again where the timeline would be: both places have to
    // answer the question, because either may be the one the user is looking at.
    expect(await screen.findAllByText(/no salary recorded/i)).not.toHaveLength(0)
  })

  // --- the timeline -------------------------------------------------------------

  it('shows the history newest first', async () => {
    detailSpy(
      aDetail({
        history: [
          aSalary({ id: 2, effective_from: '2026-10-01', amount: '1400000.000' }),
          aSalary({ id: 1, effective_from: '2026-04-01', effective_to: '2026-09-30' }),
        ],
      }),
    )
    render()

    const entries = await screen.findAllByRole('listitem')
    // Each entry carries two dates - the range and when it was recorded - so the
    // assertion is on the entry containing the date, not on a unique match.
    expect(within(entries[0]).getAllByText(/01 Oct 2026/).length).toBeGreaterThan(0)
    expect(within(entries[1]).getAllByText(/01 Apr 2026/).length).toBeGreaterThan(0)
  })

  it('says who made each change and when it was made', async () => {
    // "when it was made" is created_at, not effective_from: a raise entered in
    // November and backdated to April is one event in November.
    detailSpy(
      aDetail({
        history: [aSalary({ created_by_name: 'rahul', created_at: '2026-03-20T10:00:00Z' })],
      }),
    )
    render()

    const entry = (await screen.findAllByRole('listitem'))[0]
    expect(within(entry).getByText(/rahul/)).toBeInTheDocument()
    expect(within(entry).getByText(/20 Mar 2026/)).toBeInTheDocument()
  })

  it('shows the reason a change was made', async () => {
    detailSpy(aDetail({ history: [aSalary({ change_reason: 'PROMOTION' })] }))
    render()

    expect(await screen.findByText(/promotion/i)).toBeInTheDocument()
  })

  it('marks which entry is the one in force', async () => {
    detailSpy(
      aDetail({
        history: [
          aSalary({ id: 2, effective_to: null }),
          aSalary({ id: 1, effective_from: '2025-04-01', effective_to: '2026-03-31' }),
        ],
      }),
    )
    render()

    const entries = await screen.findAllByRole('listitem')
    expect(within(entries[0]).getByText(/current/i)).toBeInTheDocument()
  })

  // --- changing the salary --------------------------------------------------------

  it('sends amount, currency and effective date, and nothing about USD', async () => {
    // Letting the browser supply usd_amount would make every reported total a
    // client-controlled figure.
    detailSpy()
    const setSalary = vi
      .spyOn(resources.employees, 'setSalary')
      .mockResolvedValue({ changed: true, salary: aSalary({ amount: '1400000.000' }) })
    const user = userEvent.setup()
    render()

    await user.click(await screen.findByRole('button', { name: /change salary/i }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(/amount/i))
    await user.type(within(dialog).getByLabelText(/amount/i), '1400000')
    await user.type(within(dialog).getByLabelText(/effective from/i), '2026-10-01')
    await user.click(within(dialog).getByRole('button', { name: /save/i }))

    await waitFor(() => expect(setSalary).toHaveBeenCalled())
    const [, body] = setSalary.mock.calls[0]
    expect(body).toMatchObject({ amount: '1400000', currency: 'INR' })
    expect(body).not.toHaveProperty('usd_amount')
  })

  it('refreshes the history after a successful change', async () => {
    const detail = detailSpy()
    vi.spyOn(resources.employees, 'setSalary').mockResolvedValue({
      changed: true,
      salary: aSalary(),
    })
    const user = userEvent.setup()
    render()

    await user.click(await screen.findByRole('button', { name: /change salary/i }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(/amount/i))
    await user.type(within(dialog).getByLabelText(/amount/i), '1400000')
    await user.type(within(dialog).getByLabelText(/effective from/i), '2026-10-01')
    await user.click(within(dialog).getByRole('button', { name: /save/i }))

    // Twice: once on mount, once after the change. A stale timeline after an edit
    // is the user wondering whether it saved.
    await waitFor(() => expect(detail).toHaveBeenCalledTimes(2))
  })

  it('says "no change" rather than claiming a raise when the pay already matched', async () => {
    // changed: false means no row was written and no audit entry made. A green
    // success toast here is the interface telling a comfortable lie.
    detailSpy()
    vi.spyOn(resources.employees, 'setSalary').mockResolvedValue({
      changed: false,
      salary: aSalary(),
    })
    const user = userEvent.setup()
    render()

    await user.click(await screen.findByRole('button', { name: /change salary/i }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(/amount/i))
    await user.type(within(dialog).getByLabelText(/amount/i), '1200000')
    await user.type(within(dialog).getByLabelText(/effective from/i), '2026-10-01')
    await user.click(within(dialog).getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/no change/i)).toBeInTheDocument()
  })

  it('surfaces a missing FX rate as something an admin can fix', async () => {
    // Review Focus #1, at the UI. "No FX rate for XAF as of 2026-04-01" is
    // actionable; "Request failed" is not.
    detailSpy()
    vi.spyOn(resources.employees, 'setSalary').mockRejectedValue(
      new ApiError(422, 'No FX rate for INR as of 2026-04-01.'),
    )
    const user = userEvent.setup()
    render()

    await user.click(await screen.findByRole('button', { name: /change salary/i }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(/amount/i))
    await user.type(within(dialog).getByLabelText(/amount/i), '1400000')
    await user.type(within(dialog).getByLabelText(/effective from/i), '2026-10-01')
    await user.click(within(dialog).getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/no fx rate for inr/i)).toBeInTheDocument()
  })

  it('keeps the dialog open when the server refuses, so the input is not lost', async () => {
    detailSpy()
    vi.spyOn(resources.employees, 'setSalary').mockRejectedValue(
      new ApiError(422, 'Salary cannot start on 2026-01-01.'),
    )
    const user = userEvent.setup()
    render()

    await user.click(await screen.findByRole('button', { name: /change salary/i }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(/amount/i))
    await user.type(within(dialog).getByLabelText(/amount/i), '1400000')
    await user.type(within(dialog).getByLabelText(/effective from/i), '2026-10-01')
    await user.click(within(dialog).getByRole('button', { name: /save/i }))

    await screen.findByText(/cannot start on/i)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  // --- who may do what ------------------------------------------------------------

  it('lets an HR manager change pay', async () => {
    detailSpy()
    render(aDetail(), ['hr_manager'])

    expect(await screen.findByRole('button', { name: /change salary/i })).toBeInTheDocument()
  })

  it('does not offer an HR manager the admin-only edits', async () => {
    detailSpy()
    render(aDetail(), ['hr_manager'])
    await screen.findByRole('heading', { name: /priya sharma/i })

    expect(screen.queryByRole('button', { name: /edit details/i })).not.toBeInTheDocument()
  })

  it('offers an admin the details edit', async () => {
    detailSpy()
    render(aDetail(), ['admin'])

    expect(await screen.findByRole('button', { name: /edit details/i })).toBeInTheDocument()
  })

  it('does not offer to change the pay of someone who has left', async () => {
    // The service refuses it. Offering the button and failing at 422 is worse
    // manners than not offering it.
    detailSpy(
      aDetail({
        employee: anEmployee({ status: 'INACTIVE', termination_date: '2026-09-30' }),
        current_salary: null,
      }),
    )
    render()
    await screen.findByRole('heading', { name: /priya sharma/i })

    expect(screen.queryByRole('button', { name: /change salary/i })).not.toBeInTheDocument()
  })

  it('tells the user when the person could not be loaded', async () => {
    vi.spyOn(resources.employees, 'detail').mockRejectedValue(new ApiError(404, 'Not found.'))
    render()

    // Mantine's own notification region is also an alert, so this asks for the one
    // that says what went wrong rather than for "an alert".
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((a) => /could not load/i.test(a.textContent ?? ''))).toBe(true)
  })
})
