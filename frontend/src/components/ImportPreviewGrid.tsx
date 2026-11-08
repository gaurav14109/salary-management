/**
 * The file, before you import it.
 *
 * An import is all-or-nothing (ADR-0004), so a file with one bad cell on line 400
 * lands nothing. This screen is what turns that from a surprise into a decision:
 * the rows arrive already checked, the cells that would fail are marked, and the
 * Import button is only live when the file would actually go in.
 *
 * Read-only on purpose. Fixing the data belongs in the spreadsheet that is the
 * system of record - correcting a cell here would mean the file HR keeps and the
 * rows ACME imported had quietly diverged, with nothing recording which was which.
 *
 * Every value is shown as a string, exactly as the file had it. Parsing dates and
 * numbers to render them would quietly correct some mistakes and mangle others, and
 * the one thing somebody looking at a bad cell needs is to see what is really there.
 *
 * Nothing is validated here. The browser cannot know ACME's departments, its
 * currencies, or whether an FX rate exists for a date - the errors on screen are
 * the server's, from the same `validate` the import itself runs.
 *
 * ## Why this is not a plain `rows.map()`
 *
 * At the size the brief names - 10,000 employees - the obvious implementation does
 * not work: 10,000 rows of twelve columns is 120,000 cells, and the measurement
 * before this was written was 41 seconds followed by the JS heap running out. Only
 * the rows near the viewport are mounted; spacer rows stand in for the rest, so the
 * scrollbar still describes the whole file.
 */

import { Alert, Table, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useMemo, useRef } from 'react'

import type { ImportPreview } from '../api/types'
import styles from './ImportPreviewGrid.module.css'

/** Row height in px. Fixed, because a virtualiser needs it before anything mounts. */
const ROW_HEIGHT = 36

type Props = {
  preview: ImportPreview
}

/** `"3:currency"` - the key each cell looks itself up by. */
function cellKey(rowNumber: number, column: string): string {
  return `${rowNumber}:${column}`
}

export function ImportPreviewGrid({ preview }: Props) {
  const errors = useMemo(() => {
    const index: Record<string, string> = {}
    for (const error of preview.errors) {
      index[cellKey(error.row_number, error.field)] = error.message
    }
    return index
  }, [preview])

  const scrollRef = useRef<HTMLDivElement>(null)
  const virtualiser = useVirtualizer({
    count: preview.rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    // A few rows beyond the viewport, so scrolling does not reveal blank space
    // before React has rendered into it.
    overscan: 10,
  })

  const virtualRows = virtualiser.getVirtualItems()
  // Spacers stand in for the rows that are not mounted. Table rows rather than
  // absolute positioning: a table that is not made of rows loses its column
  // alignment and its row semantics.
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom =
    virtualRows.length > 0
      ? virtualiser.getTotalSize() - virtualRows[virtualRows.length - 1].end
      : 0

  // The file itself is wrong - a missing column, an empty sheet - so there are no
  // rows worth drawing.
  const fileError = preview.errors.find((error) => error.row_number === 0)
  if (fileError) {
    return (
      <Alert color="critical" icon={<IconAlertTriangle size={18} />} role="alert">
        {fileError.message}
      </Alert>
    )
  }

  return (
    <div className={styles.scroll} ref={scrollRef}>
      <Table striped={false} withTableBorder={false} className={styles.grid}>
        <Table.Thead className={styles.head}>
          <Table.Tr>
            <Table.Th className={styles.lineColumn}>Line</Table.Th>
            {preview.columns.map((column) => (
              <Table.Th key={column}>{column}</Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {paddingTop > 0 && (
            <Table.Tr aria-hidden style={{ height: paddingTop }}>
              <Table.Td colSpan={preview.columns.length + 1} />
            </Table.Tr>
          )}
          {virtualRows.map((virtualRow) => {
            const row = preview.rows[virtualRow.index]
            return (
              <Table.Tr key={row.row_number} style={{ height: ROW_HEIGHT }}>
                <Table.Td className={styles.lineColumn}>
                  <Text className="u-faint">{row.row_number}</Text>
                </Table.Td>
                {preview.columns.map((column) => {
                  const message = errors[cellKey(row.row_number, column)]
                  return (
                    <Table.Td
                      key={column}
                      className={message ? styles.badCell : undefined}
                      // The message, on the cell it belongs to. A screen reader
                      // reaching this cell hears why it is wrong, rather than
                      // hearing a value and having to find the error list.
                      title={message}
                      aria-label={message ? `${row.values[column]} — ${message}` : undefined}
                    >
                      {row.values[column]}
                    </Table.Td>
                  )
                })}
              </Table.Tr>
            )
          })}
          {paddingBottom > 0 && (
            <Table.Tr aria-hidden style={{ height: paddingBottom }}>
              <Table.Td colSpan={preview.columns.length + 1} />
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </div>
  )
}
