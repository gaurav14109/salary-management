/**
 * The employee list. Where an HR manager lands, and where the work starts.
 *
 * Search, filter, sort and paging are all the server's job. The table renders the
 * page it was given and never reorders it: sorting fifty rows client-side produces a
 * "highest paid" that is only the highest on that page - confidently wrong, and
 * invisible.
 */

import { Badge, Button, Group, Loader, Select, Table, Text, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import {
  IconAlertTriangle,
  IconChevronDown,
  IconChevronUp,
  IconDownload,
  IconPlus,
  IconSearch,
  IconSelector,
} from '@tabler/icons-react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { employees, reference, type EmployeeFilters } from '../api/resources'
import type { EmployeeListItem } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { formatLocal, formatUsd } from '../format/money'
import styles from './EmployeeListPage.module.css'

const PAGE_SIZE = 25
/** Long enough that typing a surname is one request, short enough to feel live. */
const SEARCH_DEBOUNCE_MS = 300

type Sort = { field: string; order: 'asc' | 'desc' }

export function EmployeeListPage() {
  const auth = useAuth()
  const [search, setSearch] = useState('')
  const [debouncedSearch] = useDebouncedValue(search, SEARCH_DEBOUNCE_MS)
  const [country, setCountry] = useState<string | null>(null)
  const [department, setDepartment] = useState<string | null>(null)
  const [band, setBand] = useState<string | null>(null)
  const [sort, setSort] = useState<Sort>({ field: 'last_name', order: 'asc' })
  const [page, setPage] = useState(1)

  /**
   * Any change to what is being asked for returns to page one.
   *
   * Without this, filtering to three results while on page four shows an empty
   * table - and the user reads that as "no matches" rather than "wrong page".
   */
  useEffect(() => {
    setPage(1)
  }, [debouncedSearch, country, department, band, sort])

  const filters: EmployeeFilters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      country,
      department: department ? Number(department) : null,
      band: band ? Number(band) : null,
      sort: sort.field,
      order: sort.order,
      page,
      size: PAGE_SIZE,
    }),
    [debouncedSearch, country, department, band, sort, page],
  )

  const { data: refData } = useQuery({
    queryKey: ['reference'],
    queryFn: reference.all,
    // Countries and bands change when the company reorganises, not during a session.
    staleTime: Infinity,
  })

  const { data, isPending, isError, isFetching } = useQuery({
    queryKey: ['employees', filters],
    queryFn: () => employees.list(filters),
    // Keeps the previous page on screen while the next loads, so paging does not
    // flash an empty table between two full ones.
    placeholderData: keepPreviousData,
  })

  const minorUnits = useMemo(() => {
    const map = new Map<string, number>()
    refData?.currencies.forEach((c) => map.set(c.code, c.minor_units))
    return map
  }, [refData])

  // A row carries department_id and band_id, not their names. The reference data is
  // already here for the filters, so the names are a lookup rather than a wider
  // employee payload: the alternative is denormalising a name onto every row of a
  // 10,000-row page to save a map this page already builds.
  const names = useMemo(() => {
    const departments = new Map<number, string>()
    refData?.departments.forEach((d) => departments.set(d.id, d.name))
    const bands = new Map<number, string>()
    refData?.bands.forEach((b) => bands.set(b.id, b.name))
    return { departments, bands }
  }, [refData])

  function toggleSort(field: string) {
    setSort((current) =>
      current.field === field
        ? { field, order: current.order === 'asc' ? 'desc' : 'asc' }
        : // First click on money sorts highest-first: "who earns most" is the
          // question being asked, not "who earns least".
          { field, order: field === 'usd_amount' ? 'desc' : 'asc' },
    )
  }

  const total = data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="u-page u-stack">
      <div className="u-row-between">
        <div className="u-stack-tight">
          <h1 className="u-title">Employees</h1>
          <span className="u-muted">
            {isPending ? 'Loading…' : `${total.toLocaleString()} ${total === 1 ? 'person' : 'people'}`}
          </span>
        </div>
        <Group gap="xs">
          <Button
            component="a"
            href={employees.exportUrl(filters)}
            variant="default"
            leftSection={<IconDownload size={16} stroke={1.6} />}
          >
            Export CSV
          </Button>
          {/* Creating a person is an admin act. Offering it to an HR manager and
              failing at 403 is worse manners than not offering it. */}
          {auth.hasRole('admin') && (
            <Button leftSection={<IconPlus size={16} stroke={1.6} />}>Add employee</Button>
          )}
        </Group>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.filters}>
          <TextInput
            className={styles.search}
            type="search"
            aria-label="Search employees"
            placeholder="Search name or code"
            leftSection={<IconSearch size={16} stroke={1.6} />}
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
          />
          <Select
            aria-label="Country"
            placeholder="Country"
            clearable
            searchable
            nothingFoundMessage="No match"
            value={country}
            onChange={setCountry}
            data={(refData?.countries ?? []).map((c) => ({ value: c.code, label: c.name }))}
            w={170}
          />
          <Select
            aria-label="Department"
            placeholder="Department"
            clearable
            searchable
            nothingFoundMessage="No match"
            value={department}
            onChange={setDepartment}
            data={(refData?.departments ?? []).map((d) => ({
              value: String(d.id),
              label: d.name,
            }))}
            w={170}
          />
          <Select
            aria-label="Band"
            placeholder="Band"
            clearable
            searchable
            nothingFoundMessage="No match"
            value={band}
            onChange={setBand}
            // Already ordered by rank from the API, so the dropdown reads
            // L1, L2, L10 rather than L1, L10, L2.
            data={(refData?.bands ?? []).map((b) => ({ value: String(b.id), label: b.name }))}
            w={130}
          />
        </div>
        {isFetching && !isPending && <Loader size="xs" />}
      </div>

      {isError ? (
        <div className="u-empty" role="alert">
          <IconAlertTriangle size={28} stroke={1.5} />
          {/* An empty table after a failed request reads as "no employees", which is
              a materially different and more alarming thing. */}
          <Text fw={500}>Could not load employees</Text>
          <Text size="sm">The server did not answer. Try again in a moment.</Text>
        </div>
      ) : (
        <div className={styles.tableWrap}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <SortableHeader
                  label="Employee"
                  field="last_name"
                  sort={sort}
                  onSort={toggleSort}
                />
                <Table.Th>Department</Table.Th>
                <Table.Th>Job title</Table.Th>
                <Table.Th>Band</Table.Th>
                <Table.Th>Country</Table.Th>
                <SortableHeader
                  label="Salary"
                  field="usd_amount"
                  sort={sort}
                  onSort={toggleSort}
                  numeric
                />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data?.items.map((item) => (
                <Row
                  key={item.employee.id}
                  item={item}
                  minorUnits={minorUnits}
                  names={names}
                />
              ))}
            </Table.Tbody>
          </Table>

          {!isPending && total === 0 && (
            <div className="u-empty">
              <Text fw={500}>No employees match these filters</Text>
              <Text size="sm">Clear a filter, or import a file to get started.</Text>
            </div>
          )}

          {total > PAGE_SIZE && (
            <div className={styles.footer}>
              <span className="u-muted">
                Page {page} of {lastPage}
              </span>
              <Group gap="xs">
                <Button
                  variant="default"
                  size="xs"
                  disabled={page === 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous page
                </Button>
                <Button
                  variant="default"
                  size="xs"
                  disabled={page >= lastPage}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next page
                </Button>
              </Group>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function SortableHeader({
  label,
  field,
  sort,
  onSort,
  numeric,
}: {
  label: string
  field: string
  sort: Sort
  onSort: (field: string) => void
  numeric?: boolean
}) {
  const active = sort.field === field
  const Icon = !active ? IconSelector : sort.order === 'asc' ? IconChevronUp : IconChevronDown

  return (
    <Table.Th className={numeric ? styles.numeric : undefined}>
      <Button
        variant="subtle"
        color="neutral"
        size="compact-sm"
        // Named for what it does, not what it is: a screen reader user hears
        // "sort by salary" rather than "Salary, button".
        aria-label={`Sort by ${label.toLowerCase()}`}
        rightSection={<Icon size={14} stroke={1.8} />}
        onClick={() => onSort(field)}
      >
        {label}
      </Button>
    </Table.Th>
  )
}

function Row({
  item,
  minorUnits,
  names,
}: {
  item: EmployeeListItem
  minorUnits: Map<string, number>
  names: { departments: Map<number, string>; bands: Map<number, string> }
}) {
  const { employee, current_salary: salary } = item

  return (
    <Table.Tr className={styles.row}>
      <Table.Td>
        <div className="u-stack-tight">
          {/* A real link, not a row onClick: middle-click, ctrl-click and the
              browser's status bar all stop working without an href. */}
          <Link to={`/employees/${employee.id}`} className={styles.name}>
            {employee.first_name} {employee.last_name}
          </Link>
          <span className="u-faint">{employee.employee_code}</span>
        </div>
      </Table.Td>
      <Table.Td>
        {/* The header said Department and the cell showed the job title. Two
            different facts - "Engineering" and "Staff Engineer" answer different
            questions - so they are two columns. */}
        <Text size="sm">
          {names.departments.get(employee.department_id) ?? (
            <span className="u-faint">—</span>
          )}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{employee.job_title}</Text>
      </Table.Td>
      <Table.Td>
        {employee.band_id ? (
          // The name, not the id. This printed the primary key, so the column read
          // 4 where the filter beside it reads L3.
          <Badge color="neutral" size="sm">
            {names.bands.get(employee.band_id) ?? employee.band_id}
          </Badge>
        ) : (
          <span className="u-faint">—</span>
        )}
      </Table.Td>
      <Table.Td>
        <Text size="sm">{employee.country_code}</Text>
      </Table.Td>
      <Table.Td className={styles.numeric}>
        {salary ? (
          <div className="u-stack-tight">
            <span className="u-money">{formatUsd(salary.usd_amount)}</span>
            <span className="u-money-secondary">
              {formatLocal(salary.amount, salary.currency, minorUnits.get(salary.currency))}
            </span>
          </div>
        ) : (
          // Not a blank cell. A new hire with no pay row is exactly who this list is
          // used to find, so the absence is the call to action.
          <Link to={`/employees/${employee.id}`} className="u-missing">
            Set salary
          </Link>
        )}
      </Table.Td>
    </Table.Tr>
  )
}
