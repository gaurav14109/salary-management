/**
 * Aggregate payroll, in USD.
 *
 * Two properties carry these tests. Every figure is labelled USD, because an
 * unlabelled 180,018 beside a column of rupees is a number someone will read wrong.
 * And a filter applies to every figure at once - a headline total describing one set
 * beside a chart describing another is worse than no chart.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DashboardPage } from './DashboardPage'
import { ApiError } from '../api/client'
import * as resources from '../api/resources'
import { aDashboard, anEmptyDashboard, referenceData } from '../test/fixtures'
import { renderPage, selectOption } from '../test/render'

function summarySpy(data = aDashboard()) {
  return vi.spyOn(resources.dashboard, 'summary').mockResolvedValue(data)
}

beforeEach(() => {
  vi.spyOn(resources.reference, 'all').mockResolvedValue(referenceData())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DashboardPage', () => {
  it('renders the headline figures in USD', async () => {
    summarySpy()
    renderPage(<DashboardPage />)

    expect(await screen.findByText('$180,017.97')).toBeInTheDocument()
    expect(screen.getByText('$45,004.49')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('reports the median beside the mean', async () => {
    // They differ - 35,006 against 45,004 - and that difference is the point: one
    // outlier moves the mean and leaves the median alone.
    summarySpy()
    renderPage(<DashboardPage />)

    expect(await screen.findByText('$35,005.99')).toBeInTheDocument()
    expect(screen.getByText(/median/i)).toBeInTheDocument()
  })

  it('names the highest and lowest paid, not just the figures', async () => {
    summarySpy()
    renderPage(<DashboardPage />)

    expect(await screen.findByText(/lukas weber/i)).toBeInTheDocument()
    expect(screen.getByText(/priya sharma/i)).toBeInTheDocument()
  })

  it('says the currency on the headline figures', async () => {
    summarySpy()
    renderPage(<DashboardPage />)

    expect(await screen.findAllByText(/usd/i)).not.toHaveLength(0)
  })

  it('shows payroll by country', async () => {
    summarySpy()
    renderPage(<DashboardPage />)

    // 'Germany' is also a chart axis label and a filter option, so the assertion
    // is scoped to the table that is supposed to carry the figures.
    const table = await screen.findByRole('table', { name: /country/i })
    expect(within(table).getByText('Germany')).toBeInTheDocument()
    expect(within(table).getByText('India')).toBeInTheDocument()
  })

  it('shows average pay by band in rank order', async () => {
    // Sorted by name they read L1, L10, L11, L2. The API already orders by rank;
    // this asserts the page does not re-sort them into nonsense.
    summarySpy()
    renderPage(<DashboardPage />)

    const bands = await screen.findByRole('table', { name: /band/i })
    const rows = within(bands).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      'L3',
      'L5',
      'L7',
    ])
  })

  it('shows what payroll is denominated in', async () => {
    // "How exposed is payroll to the rupee" is a question USD totals cannot answer.
    summarySpy()
    renderPage(<DashboardPage />)

    expect(await screen.findByText('INR')).toBeInTheDocument()
    expect(screen.getByText('EUR')).toBeInTheDocument()
  })

  it('applies a country filter to every figure', async () => {
    const summary = summarySpy()
    const user = userEvent.setup()
    renderPage(<DashboardPage />)
    await screen.findByText('$180,017.97')
    await waitFor(() => expect(resources.reference.all).toHaveBeenCalled())

    summary.mockResolvedValue(
      aDashboard({
        employee_count: 2,
        total_payroll_usd: '150000.00',
        by_country: [
          {
            country_code: 'DE',
            name: 'Germany',
            employee_count: 2,
            total_usd: '150000.00',
            average_usd: '75000.00',
          },
        ],
      }),
    )
    await selectOption(user, 'Country', 'Germany')

    await waitFor(() => expect(summary).toHaveBeenLastCalledWith({ country: 'DE', department: null, band: null }))
    // The filtered total, now shown in the headline card and in the country table -
    // both correct, so the assertion is that it appears, not that it appears once.
    expect(await screen.findAllByText('$150,000.00')).not.toHaveLength(0)
  })

  it('lets a filter be typed, not only picked from a list', async () => {
    /* 45 countries in a dropdown is a scroll, not a choice. Typing narrows it, which
       is the difference between "where is Germany" and "type ger". */
    summarySpy()
    const user = userEvent.setup()
    renderPage(<DashboardPage />)
    await screen.findByText('$180,017.97')
    await waitFor(() => expect(resources.reference.all).toHaveBeenCalled())

    const country = screen.getByRole('combobox', { name: 'Country' })
    await user.click(country)
    await user.type(country, 'ger')

    await waitFor(() => {
      const shown = Array.from(
        document.querySelectorAll<HTMLElement>('[data-combobox-option]'),
      ).map((element) => element.textContent?.trim())
      expect(shown).toContain('Germany')
      expect(shown).not.toContain('India')
    })
  })

  it('applies the filter that was typed, not just the text', async () => {
    /* A searchable Select that filters the list but does not commit the choice is a
       search box that does nothing. */
    const summary = summarySpy()
    const user = userEvent.setup()
    renderPage(<DashboardPage />)
    await screen.findByText('$180,017.97')
    await waitFor(() => expect(resources.reference.all).toHaveBeenCalled())

    const country = screen.getByRole('combobox', { name: 'Country' })
    await user.click(country)
    await user.type(country, 'ger')
    await selectOption(user, 'Country', 'Germany')

    await waitFor(() =>
      expect(summary).toHaveBeenLastCalledWith({ country: 'DE', department: null, band: null }),
    )
  })

  it('renders a zero state without crashing', async () => {
    // Day one, before anything is imported. SUM over no rows is null and MAX returns
    // nothing - the page still has to render, and say so plainly.
    summarySpy(anEmptyDashboard())
    renderPage(<DashboardPage />)

    expect(await screen.findByText(/no salary data yet/i)).toBeInTheDocument()
  })

  it('does not claim a highest earner when there is nobody', async () => {
    summarySpy(anEmptyDashboard())
    renderPage(<DashboardPage />)

    await screen.findByText(/no salary data yet/i)
    expect(screen.queryByText(/highest paid/i)).not.toBeInTheDocument()
  })

  it('tells the user when the figures could not be loaded', async () => {
    // An empty dashboard after a failed request reads as "the company has no
    // payroll", which is alarming and false.
    vi.spyOn(resources.dashboard, 'summary').mockRejectedValue(new ApiError(500, 'boom'))
    renderPage(<DashboardPage />)

    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((a) => /could not load/i.test(a.textContent ?? ''))).toBe(true)
  })
})
