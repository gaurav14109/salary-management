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
import type { ImportJob, ImportPreview } from '../api/types'
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

})

// --- the queue -------------------------------------------------------------------

describe('a queued import', () => {
  /**
   * The upload now returns a job in PENDING and the work happens in a worker, so
   * the page has to follow it. What a user needs to see is which of the four states
   * they are in and, when it ends badly, why.
   */

  function job(overrides: Partial<ImportJob> = {}): ImportJob {
    return { ...anImportJob(), status: 'PENDING', total_rows: 0, ...overrides }
  }

  it('refreshes the history once the import has actually finished', async () => {
    /* Changed by the queue. Invalidating when the file was *queued* would refresh
       the employee list before a single row had been applied, so the user would
       see the old data and conclude the import had done nothing. */
    const history = historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue({
      ...anImportJob(),
      status: 'PENDING',
    })
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(anImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)
    await screen.findByLabelText('2 employees created')

    await waitFor(() => expect(history).toHaveBeenCalledTimes(2))
  })

  it('says the file is queued rather than claiming it imported', async () => {
    /* The old page announced a result. Announcing one now would be a lie: nothing
       has been read yet. */
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue(job())
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(job())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText(/queued/i)).toBeInTheDocument()
  })

  it('polls until the job finishes, then shows what it did', async () => {
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue(job())
    const detail = vi
      .spyOn(resources.imports, 'detail')
      .mockResolvedValueOnce(job({ status: 'PROCESSING' }))
      .mockResolvedValue(anImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByLabelText('2 employees created')).toBeInTheDocument()
    expect(detail).toHaveBeenCalled()
  })

  it('says it is being applied while the worker has it', async () => {
    /* PROCESSING is a different thing from PENDING and the difference matters to
       somebody watching: one means "nothing is happening yet", the other means
       "do not close this". */
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue(job())
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(job({ status: 'PROCESSING' }))
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText('Importing people.csv')).toBeInTheDocument()
  })

  it('shows why a file failed when the reason is not a row error', async () => {
    /* An unreadable file has no row to point at. Without this the page shows "0
       errors" beside the word FAILED, which tells the user nothing. */
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue(job())
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(
      job({
        status: 'FAILED',
        failure_reason: 'That file is not a readable spreadsheet.',
        errors: [],
      }),
    )
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText(/not a readable spreadsheet/)).toBeInTheDocument()
  })

  it('stops polling once the job reaches a terminal state', async () => {
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue(job())
    const detail = vi.spyOn(resources.imports, 'detail').mockResolvedValue(anImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)
    await screen.findByLabelText('2 employees created')

    const callsWhenDone = detail.mock.calls.length
    await new Promise((resolve) => setTimeout(resolve, 150))

    expect(detail.mock.calls.length).toBe(callsWhenDone)
  })

  it('still reports row errors from a failed job', async () => {
    historySpy([])
    previewSpy()
    vi.spyOn(resources.imports, 'upload').mockResolvedValue(job())
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(aFailedImportJob())
    const user = userEvent.setup()
    renderPage(<ImportPage />)

    await pickAndImport(user)

    expect(await screen.findByText(/nothing was imported/i)).toBeInTheDocument()
  })
})

// --- an import already running -----------------------------------------------------

describe('an import that was started elsewhere', () => {
  /**
   * The id of the job being followed used to live in this component's state, so
   * leaving the page threw it away: a user who switched tabs mid-import came back
   * to a page that said nothing was happening, while the worker was still applying
   * their file. The in-flight job comes from the server now, not from the click
   * that started it.
   */

  function running(overrides: Partial<ImportJob> = {}): ImportJob {
    return anImportJob({
      status: 'PROCESSING',
      total_rows: 0,
      success_rows: 0,
      finished_at: null,
      ...overrides,
    })
  }

  it('shows the file being imported on a page that was just opened', async () => {
    const job = running({ id: 21, filename: 'october-payroll.csv' })
    historySpy([job])
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(job)
    renderPage(<ImportPage />)

    expect(await screen.findByText(/importing october-payroll\.csv/i)).toBeInTheDocument()
  })

  it('shows a file still waiting its turn', async () => {
    const job = running({ id: 22, filename: 'november.csv', status: 'PENDING' })
    historySpy([job])
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(job)
    renderPage(<ImportPage />)

    expect(await screen.findByText(/november\.csv is queued/i)).toBeInTheDocument()
  })

  it('follows the oldest unfinished file, which is the one the worker has', async () => {
    /* One at a time, so with two in flight the newest is queued behind the other.
       Reporting that one would say "queued" while a file was visibly importing. */
    const detail = vi.spyOn(resources.imports, 'detail').mockResolvedValue(running({ id: 30 }))
    historySpy([
      running({ id: 31, filename: 'second.csv', status: 'PENDING' }),
      running({ id: 30, filename: 'first.csv' }),
    ])
    renderPage(<ImportPage />)

    await waitFor(() => expect(detail).toHaveBeenCalledWith(30))
  })

  it('says how many files are behind the one being applied', async () => {
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(running({ id: 30 }))
    historySpy([
      running({ id: 32, filename: 'third.csv', status: 'PENDING' }),
      running({ id: 31, filename: 'second.csv', status: 'PENDING' }),
      running({ id: 30, filename: 'first.csv' }),
    ])
    renderPage(<ImportPage />)

    expect(await screen.findByText(/2 other files are waiting behind it/i)).toBeInTheDocument()
  })

  it('reports an unfinished job in the history as queued, not as a failure', async () => {
    /* The column read every non-SUCCESS job as "N errors", so a file that had not
       started yet appeared in the history as a failure with zero errors. */
    historySpy([running({ id: 40, filename: 'waiting.csv', status: 'PENDING' })])
    vi.spyOn(resources.imports, 'detail').mockResolvedValue(running({ id: 40 }))
    renderPage(<ImportPage />)

    const table = await screen.findByRole('table', { name: /previous imports/i })
    const row = within(table).getAllByRole('row')[1]
    expect(within(row).getByText(/queued/i)).toBeInTheDocument()
    expect(within(row).queryByText(/errors/i)).not.toBeInTheDocument()
  })

  it('keeps showing the result after the import it adopted has finished', async () => {
    /* The job drops out of the in-flight list the moment it succeeds. Dropping the
       card with it would mean the user who came back to watch never sees how it
       went. */
    historySpy([running({ id: 50 })])
    vi.spyOn(resources.imports, 'detail')
      .mockResolvedValueOnce(running({ id: 50 }))
      .mockResolvedValue(anImportJob({ id: 50 }))
    renderPage(<ImportPage />)

    // Longer than the 1 s poll: the second answer only arrives on the next tick.
    expect(
      await screen.findByLabelText('2 employees created', {}, { timeout: 3000 }),
    ).toBeInTheDocument()
  })
})
