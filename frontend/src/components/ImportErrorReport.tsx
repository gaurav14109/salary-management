/**
 * Why an import was rejected, row by row.
 *
 * This is the feature. An import that fails and says only "failed" sends someone
 * back to a ten-thousand-row spreadsheet with no idea where to look, and they will
 * not use the feature again.
 *
 * Two things the table has to get right: the row number must match what Excel shows
 * in its margin (header counted), and a problem with the file itself must not be
 * dressed up as a problem with row 0.
 */

import { Alert, Table, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'

import type { ImportRowError } from '../api/types'

export function ImportErrorReport({ errors }: { errors: ImportRowError[] }) {
  if (errors.length === 0) return null

  return (
    <div className="u-stack">
      <Alert color="critical" variant="light" icon={<IconAlertTriangle size={18} />}>
        {/* The most important sentence on the page. Without it the user assumes a
            partial import and starts reconciling rows by hand. */}
        <Text fw={600}>Nothing was imported.</Text>
        <Text size="sm">
          The whole file is rejected when any row fails, so your data is unchanged. Fix
          the rows below and upload again.
        </Text>
      </Alert>

      <Table aria-label="Import errors" striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={110}>Row</Table.Th>
            <Table.Th w={160}>Field</Table.Th>
            <Table.Th>Problem</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {errors.map((error, index) => (
            <Table.Tr key={`${error.row_number}-${error.field}-${index}`}>
              <Table.Td>
                {/* 0 means the file, not a line. Saying "row 0" would send someone
                    looking for a line that does not exist. */}
                {error.row_number === 0 ? (
                  <Text size="sm" c="dimmed">
                    Whole file
                  </Text>
                ) : (
                  <span className="u-money">{error.row_number}</span>
                )}
              </Table.Td>
              <Table.Td>
                <Text size="sm" ff="var(--font-mono)">
                  {error.field}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm">{error.message}</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Text size="xs" c="dimmed">
        Row numbers match the file as Excel shows it, with the header as row 1.
      </Text>
    </div>
  )
}
