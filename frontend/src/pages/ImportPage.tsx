/**
 * Bulk import.
 *
 * Synchronous: the response is the finished result, not a job id to poll. Ten
 * thousand rows is seconds, and a queue would add a broker, a worker and a polling
 * UI to avoid a wait nobody has complained about.
 *
 * All-or-nothing, and the page has to say so. Half an import is worse than none
 * because nobody can tell which half landed - so a failed upload leads with
 * "nothing was imported" before it shows a single error.
 */

import { Alert, Button, Card, Group, Table, Text } from '@mantine/core'
import { Dropzone } from '@mantine/dropzone'
import {
  IconAlertTriangle,
  IconCheck,
  IconFileSpreadsheet,
  IconTemplate,
  IconUpload,
  IconX,
} from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { ApiError } from '../api/client'
import { imports } from '../api/resources'
import type { ImportJob, ImportPreview } from '../api/types'
import { ImportErrorReport } from '../components/ImportErrorReport'
import { ImportPreviewGrid } from '../components/ImportPreviewGrid'
import { formatDate } from '../format/money'
import styles from './ImportPage.module.css'

/** Exactly the columns the importer requires, in the order it documents them. */
const TEMPLATE_COLUMNS = [
  'employee_code',
  'first_name',
  'last_name',
  'email',
  'country_code',
  'department',
  'job_title',
  'band',
  'hire_date',
  'amount',
  'currency',
  'effective_from',
]

const TEMPLATE_EXAMPLE = [
  'EMP-1042',
  'Priya',
  'Sharma',
  'priya@acme.test',
  'IN',
  'Engineering',
  'Engineer',
  'L3',
  '2024-04-01',
  '1200000',
  'INR',
  '2026-04-01',
]

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024

export function ImportPage() {
  const queryClient = useQueryClient()
  const [result, setResult] = useState<ImportJob | null>(null)
  // The file as the server read it, plus its errors. Held so the grid can be shown
  // and the original kept for the "import the whole file" path, where the preview
  // is only a sample.
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [file, setFile] = useState<File | null>(null)

  const { data: history } = useQuery({
    queryKey: ['imports'],
    queryFn: () => imports.list(),
  })

  /**
   * Step one: read the file and check it, writing nothing.
   *
   * A drop no longer imports. The first thing that happens to a file is that the
   * user sees it - which is the whole point, since an all-or-nothing import that
   * fails on line 400 otherwise costs a round trip through Excel.
   */
  const check = useMutation({
    mutationFn: (dropped: File) => imports.preview(dropped),
    onSuccess: (fresh) => {
      setPreview(fresh)
      setResult(null)
    },
  })

  const upload = useMutation({
    mutationFn: (file: File) => imports.upload(file),
    onSuccess: (job) => {
      setResult(job)
      setPreview(null)
      void queryClient.invalidateQueries({ queryKey: ['imports'] })
      // An import changes employees and salaries, so every list showing them is now
      // stale. Leaving them cached is the user wondering whether the import worked.
      void queryClient.invalidateQueries({ queryKey: ['employees'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  /**
   * The template, built in the browser.
   *
   * A header row and one example. Generated here rather than fetched because it is
   * a constant, and an endpoint for a constant is an endpoint that can be down.
   */
  const templateHref = useMemo(() => {
    const csv = `${TEMPLATE_COLUMNS.join(',')}\n${TEMPLATE_EXAMPLE.join(',')}\n`
    return `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`
  }, [])

  const startOver = () => {
    setPreview(null)
    setResult(null)
    setFile(null)
  }

  const failed = result?.status === 'FAILED'

  return (
    <div className="u-page u-stack">
      <div className="u-row-between">
        <div className="u-stack-tight">
          <h1 className="u-title">Import</h1>
          <span className="u-muted">
            Upload a .csv or .xlsx of employees and their pay. The whole file is applied,
            or none of it.
          </span>
        </div>
        <Button
          component="a"
          href={templateHref}
          download="salary-import-template.csv"
          variant="default"
          leftSection={<IconTemplate size={16} stroke={1.6} />}
        >
          Download template
        </Button>
      </div>

      <div className={styles.layout}>
        <div className="u-stack">
          <Card p={0}>
            <Dropzone
              onDrop={(files) => {
                if (!files[0]) return
                setFile(files[0])
                check.mutate(files[0])
              }}
              loading={check.isPending}
              maxSize={MAX_UPLOAD_BYTES}
              accept={[
                'text/csv',
                'application/vnd.ms-excel',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              ]}
            >
              <div className={styles.dropzoneInner}>
                <Dropzone.Accept>
                  <IconUpload size={36} stroke={1.3} color="var(--c-brand)" />
                </Dropzone.Accept>
                <Dropzone.Reject>
                  <IconX size={36} stroke={1.3} color="var(--c-critical)" />
                </Dropzone.Reject>
                <Dropzone.Idle>
                  <IconFileSpreadsheet size={36} stroke={1.3} color="var(--c-text-faint)" />
                </Dropzone.Idle>
                <Text fw={550}>Drop a file here, or click to choose one</Text>
                <Text size="sm" c="dimmed">
                  .csv or .xlsx · up to 10 MB · up to 20,000 rows
                </Text>
              </div>
            </Dropzone>
          </Card>

          {check.error && (
            <Alert color="critical" variant="light" icon={<IconAlertTriangle size={18} />}>
              {check.error instanceof ApiError
                ? check.error.message
                : 'That file could not be read.'}
            </Alert>
          )}

          {preview && !result && (
            <Card>
              <div className="u-row-between">
                <span className="u-label">
                  {file?.name ?? 'Preview'} — review and fix before importing
                </span>
                <Button variant="subtle" size="compact-sm" onClick={startOver}>
                  Choose a different file
                </Button>
              </div>
              <ImportPreviewGrid preview={preview} />
              <Group justify="space-between" mt="md">
                <Text className="u-muted">
                  {preview.row_count.toLocaleString()}{' '}
                  {preview.row_count === 1 ? 'row' : 'rows'},{' '}
                  {preview.error_count.toLocaleString()}{' '}
                  {preview.error_count === 1 ? 'error' : 'errors'}
                  {preview.truncated && ` · showing the first ${preview.rows.length}`}
                </Text>
                <Button
                  leftSection={<IconUpload size={16} />}
                  onClick={() => file && upload.mutate(file)}
                  loading={upload.isPending}
                  // The server would refuse anyway. Letting it through would cost an
                  // upload and produce a second error report saying what is already
                  // on screen.
                  disabled={preview.error_count > 0}
                >
                  Import {preview.row_count.toLocaleString()} rows
                </Button>
              </Group>
            </Card>
          )}

          {upload.error && (
            <Alert color="critical" variant="light" icon={<IconAlertTriangle size={18} />}>
              {/* The server's own sentence. "That file is 12.4 MB, the limit is 10 MB"
                  is actionable; "upload failed" is not. */}
              {upload.error instanceof ApiError
                ? upload.error.message
                : 'That file could not be uploaded.'}
            </Alert>
          )}

          {result && !failed && (
            <Card>
              <Alert color="positive" variant="light" icon={<IconCheck size={18} />}>
                <Text fw={600}>Imported {result.filename}</Text>
                <Text size="sm">{result.total_rows.toLocaleString()} rows read.</Text>
              </Alert>

              {/* Four counts, not one. "1,000 rows succeeded" hides what people
                  actually ask after an import. */}
              <div className={styles.counts}>
                <Count
                  value={result.employees_created}
                  one="employee created"
                  many="employees created"
                />
                <Count
                  value={result.employees_updated}
                  one="employee updated"
                  many="employees updated"
                />
                <Count
                  value={result.salaries_created}
                  one="salary recorded"
                  many="salaries recorded"
                />
                <Count
                  value={result.salaries_unchanged}
                  one="salary unchanged"
                  many="salaries unchanged"
                  hint="Pay already matched, so nothing was written"
                />
              </div>
            </Card>
          )}

          {failed && result && <ImportErrorReport errors={result.errors} />}
        </div>

        <Card>
          <span className="u-label">Previous imports</span>
          {history && history.items.length > 0 ? (
            <Table mt="md" aria-label="Previous imports">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>File</Table.Th>
                  <Table.Th>When</Table.Th>
                  <Table.Th>Result</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {history.items.map((job) => (
                  <Table.Tr key={job.id}>
                    <Table.Td>
                      <Text size="sm">{job.filename}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" c="dimmed">
                        {formatDate(job.created_at)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text
                        size="xs"
                        c={job.status === 'SUCCESS' ? 'positive.7' : 'critical.6'}
                        fw={600}
                      >
                        {job.status === 'SUCCESS'
                          ? `${job.success_rows} rows`
                          : `${job.error_rows} errors`}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : (
            <Text size="sm" c="dimmed" mt="sm">
              No imports yet.
            </Text>
          )}
        </Card>
      </div>
    </div>
  )
}

function Count({
  value,
  one,
  many,
  hint,
}: {
  value: number
  one: string
  many: string
  hint?: string
}) {
  // Both forms spelled out rather than derived. Stripping a trailing "s" turns
  // "salaries" into "salarie", and a plural rule is not worth being clever about.
  const label = value === 1 ? one : many

  return (
    // The number and its label are two elements for layout, but one phrase for
    // anyone reading the page aloud - a tile announcing "2" and then "employees
    // created" separately is two facts the listener has to join up.
    <div className={styles.count} aria-label={`${value} ${label}`}>
      <div className={styles.countValue} aria-hidden="true">
        {value.toLocaleString()}
      </div>
      <Text size="xs" c="dimmed" aria-hidden="true">
        {label}
      </Text>
      {hint && (
        <Text size="xs" c="dimmed" mt={2}>
          {hint}
        </Text>
      )}
    </div>
  )
}
