/**
 * Bulk import.
 *
 * The error report is the feature. A failed import that says only "failed" sends
 * someone back to a ten-thousand-row spreadsheet with no idea where to look - so
 * every rejection carries its row number and field, and the page says plainly that
 * nothing was written.
 *
 * The other half is the counts: "1,000 rows succeeded" hides the thing people
 * actually ask, which is how many got a raise and how many were already correct.
 */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ImportPage } from './ImportPage'
import { ApiError } from '../api/client'
import * as resources from '../api/resources'
import type { ImportPreview } from '../api/types'
import { aFailedImportJob, anImportJob } from '../test/fixtures'
import { renderPage } from '../test/render'

function uploadSpy(job = anImportJob()) {
  return vi.spyOn(resources.imports, 'upload').mockResolvedValue(job)
}

/**
 * Dropping a file no longer imports it - it previews it. Every test that is about
 * what an import does has to walk through the preview to get there, which is the
 * flow a user now has.
 */
function previewSpy(overrides: Partial<ImportPreview> = {}) {
  const preview: ImportPreview = {
    columns: ['employee_code', 'first_name'],
    rows: [{ row_number: 2, values: { employee_code: 'EMP-1', first_name: 'Anita' } }],
    errors: [],
    row_count: 1,
    error_count: 0,
    truncated: false,
    ...overrides,
  }
  return vi.spyOn(resources.imports, 'preview').mockResolvedValue(preview)
}

/** Drop a file, wait for the grid, then press Import. */
async function pickAndImport(user: ReturnType<typeof userEvent.setup>, name = 'people.csv') {
  const file = await pickFile(user, name)
  await screen.findByRole('table')
  await user.click(screen.getByRole('button', { name: /^import \d/i }))
  return file
}

function historySpy(items = [anImportJob()]) {
  return vi
    .spyOn(resources.imports, 'list')
    .mockResolvedValue({ items, total: items.length, page: 1, size: 20 })
}

async function pickFile(user: ReturnType<typeof userEvent.setup>, name = 'people.csv') {
  const file = new File(['employee_code\nEMP-1'], name, { type: 'text/csv' })
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await user.upload(input, file)
  return file
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ImportPage', () => {
  it('previews the chosen file rather than importing it', async () => {
    /* The drop is no longer the commit. A file whose line 400 is wrong would
       otherwise import, fail, and leave the user to find the line in Excel. */
    historySpy([])
    const preview = previewSpy()
    const upload = uploadSpy()
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    const file = await pickFile(user)

    await waitFor(() => expect(preview).toHaveBeenCalledWith(file))
    expect(upload).not.toHaveBeenCalled()
  })

  it('imports the file that was previewed, once the user confirms', async () => {
    /* The same file, not a reconstruction of it. The preview is a view; the bytes
       that get imported are the bytes HR exported. */
    historySpy([])
    previewSpy()
    const upload = uploadSpy()
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    const file = await pickAndImport(user)

    await waitFor(() => expect(upload).toHaveBeenCalledWith(file))
  })

  it('reports what the import actually did, not just that it succeeded', async () => {
    // Created, updated and unchanged are three different outcomes, and the third is
    // the one that makes re-uploading an exported file safe.
    historySpy([])
    previewSpy()
    uploadSpy()
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    // Read as phrases, the way the page announces them - "2" and "employees
    // created" as separate nodes would be two facts the reader has to join up.
    expect(await screen.findByLabelText('2 employees created')).toBeInTheDocument()
    expect(screen.getByLabelText('1 employee updated')).toBeInTheDocument()
    expect(screen.getByLabelText('1 salary unchanged')).toBeInTheDocument()
  })

  it('renders every row error with its row number and field', async () => {
    // Review Focus #2 at the UI. Row 2 is the first data line, because row 1 is the
    // header - off by one here sends someone to the wrong line.
    historySpy([])
    previewSpy()
    uploadSpy(aFailedImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    const table = await screen.findByRole('table', { name: /errors/i })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('2')).toBeInTheDocument()
    expect(within(rows[0]).getByText('currency')).toBeInTheDocument()
    expect(within(rows[0]).getByText(/unknown currency: xyz/i)).toBeInTheDocument()
  })

  it('states clearly that nothing was imported when there are errors', async () => {
    // The single most important sentence on the page. Without it the user assumes a
    // partial import and starts reconciling rows by hand.
    historySpy([])
    previewSpy()
    uploadSpy(aFailedImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText(/nothing was imported/i)).toBeInTheDocument()
  })

  it('shows which line of the file to fix, counted the way Excel counts', async () => {
    historySpy([])
    previewSpy()
    uploadSpy(aFailedImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText(/row numbers match the file/i)).toBeInTheDocument()
  })

  it('reports a whole-file problem without pretending it belongs to a row', async () => {
    // row_number 0 means the file itself - a missing column, an empty sheet.
    historySpy([])
    previewSpy()
    uploadSpy(
      aFailedImportJob({
        errors: [{ row_number: 0, field: 'file', message: 'Missing required column(s): amount.' }],
      }),
    )
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    const table = await screen.findByRole('table', { name: /errors/i })
    expect(within(table).getByText(/whole file/i)).toBeInTheDocument()
  })

  it('offers a template so nobody has to guess the columns', async () => {
    historySpy([])
    renderPage(<ImportPage />)

    expect(await screen.findByRole('link', { name: /template/i })).toBeInTheDocument()
  })

  it('surfaces a refused upload as a readable message', async () => {
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockRejectedValue(
      new ApiError(413, 'That file is 12.4 MB. The limit is 10 MB.'),
    )
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText(/the limit is 10 mb/i)).toBeInTheDocument()
  })

  it('lists previous imports, newest first', async () => {
    historySpy([
      anImportJob({ id: 9, filename: 'october.csv' }),
      anImportJob({ id: 8, filename: 'september.csv' }),
    ])
    renderPage(<ImportPage />)

    const table = await screen.findByRole('table', { name: /previous imports/i })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('october.csv')).toBeInTheDocument()
  })

  it('refreshes the history after an upload', async () => {
    const list = historySpy([])
    uploadSpy()
    previewSpy()
    const user = userEvent.setup()
    renderPage(<ImportPage />)
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))

    await pickAndImport(user)

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  })
})
