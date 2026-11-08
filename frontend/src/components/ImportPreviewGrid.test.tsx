/**
 * The read-only preview grid.
 *
 * What it is for: an import is all-or-nothing, so a file with one bad cell lands
 * nothing. Seeing the file - and the cells that would fail - before pressing Import
 * is the difference between a decision and a surprise.
 *
 * Two properties carry these tests. A bad cell has to be findable without reading
 * 10,000 rows, and the grid has to stay usable at the size the brief names: the
 * naive version was measured at 41 seconds and then exhausted the JS heap.
 */

import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ImportPreviewGrid } from './ImportPreviewGrid'
import type { ImportPreview } from '../api/types'
import { renderPage } from '../test/render'

function aPreview(overrides: Partial<ImportPreview> = {}): ImportPreview {
  return {
    columns: ['employee_code', 'first_name', 'currency'],
    rows: [
      { row_number: 2, values: { employee_code: 'EMP-1', first_name: 'Anita', currency: 'INR' } },
      { row_number: 3, values: { employee_code: 'EMP-2', first_name: 'Omar', currency: 'XYZ' } },
    ],
    errors: [{ row_number: 3, field: 'currency', message: 'Unknown currency: XYZ.' }],
    row_count: 2,
    error_count: 1,
    truncated: false,
    ...overrides,
  }
}

function aBigPreview(rowCount = 10_000): ImportPreview {
  return {
    columns: ['employee_code', 'first_name', 'currency'],
    rows: Array.from({ length: rowCount }, (_, i) => ({
      row_number: i + 2,
      values: { employee_code: `EMP-${i}`, first_name: `Person${i}`, currency: 'INR' },
    })),
    errors: [],
    row_count: rowCount,
    error_count: 0,
    truncated: false,
  }
}

describe('ImportPreviewGrid', () => {
  it('shows the file as a table, one row per line', () => {
    renderPage(<ImportPreviewGrid preview={aPreview()} />)

    expect(screen.getByText('EMP-1')).toBeInTheDocument()
    expect(screen.getByText('Omar')).toBeInTheDocument()
  })

  it('shows the spreadsheet line number, header counted', () => {
    /* Row 2 is the first row of data. An off-by-one sends the user to the wrong
       line in their own file. */
    renderPage(<ImportPreviewGrid preview={aPreview()} />)

    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('shows every column the file had', () => {
    renderPage(<ImportPreviewGrid preview={aPreview()} />)

    expect(screen.getByText('employee_code')).toBeInTheDocument()
    expect(screen.getByText('currency')).toBeInTheDocument()
  })

  it('marks the cell that is wrong, not just the row', () => {
    renderPage(<ImportPreviewGrid preview={aPreview()} />)

    expect(screen.getByLabelText('XYZ — Unknown currency: XYZ.')).toBeInTheDocument()
  })

  it('leaves a good cell unmarked', () => {
    renderPage(<ImportPreviewGrid preview={aPreview()} />)

    expect(screen.queryByLabelText(/INR — /)).not.toBeInTheDocument()
  })

  it('shows the values as text, not as inputs', () => {
    /* Read-only on purpose: fixing the data belongs in the spreadsheet that is the
       system of record, or the file HR keeps and the rows ACME imported diverge
       with nothing recording which was which. */
    renderPage(<ImportPreviewGrid preview={aPreview()} />)

    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
  })

  it('reports a file-level problem rather than drawing an empty grid', () => {
    /* row_number 0 means the file itself is wrong - a missing column, an empty
       sheet. There are no rows to show. */
    renderPage(
      <ImportPreviewGrid
        preview={aPreview({
          rows: [],
          columns: [],
          row_count: 0,
          error_count: 1,
          errors: [
            { row_number: 0, field: 'file', message: 'Missing required column(s): amount.' },
          ],
        })}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Missing required column(s): amount.')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  describe('at ten thousand rows', () => {
    it('mounts a window of rows, not ten thousand of them', () => {
      /* The naive version was measured at 41 seconds and then ran out of heap. The
         assertion is the order of magnitude: tens of rows, not thousands. */
      renderPage(<ImportPreviewGrid preview={aBigPreview()} />)

      const rows = screen.getAllByRole('row')
      expect(rows.length).toBeGreaterThan(1)
      expect(rows.length).toBeLessThan(100)
    })

    it('still scrolls the height of the whole file', () => {
      /* The window is a rendering detail. A scrollbar describing 30 rows when the
         file has 10,000 would be lying about how much is there. */
      renderPage(<ImportPreviewGrid preview={aBigPreview()} />)

      const spacers = screen
        .getAllByRole('row', { hidden: true })
        .filter((row) => row.getAttribute('aria-hidden') === 'true')
      const total = spacers.reduce(
        (sum, row) => sum + Number.parseInt(row.style.height || '0', 10),
        0,
      )
      // 10,000 rows at 36px each, less the handful actually mounted.
      expect(total).toBeGreaterThan(300_000)
    })

    it('renders the first rows of the file', () => {
      renderPage(<ImportPreviewGrid preview={aBigPreview()} />)

      expect(screen.getByText('EMP-0')).toBeInTheDocument()
    })
  })
})
