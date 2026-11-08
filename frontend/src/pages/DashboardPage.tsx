/**
 * Aggregate payroll.
 *
 * Every figure is USD and says so. Nothing on this page is computed in the browser:
 * the API sums `salary.usd_amount` - the column frozen at write time - in SQL, over
 * the view that already excludes leavers and superseded rows. A total added up here
 * would be a second answer to a question that already has one.
 *
 * One request, not six. Six would mean six chances for one to fail and leave a
 * headline total beside a chart describing a different set.
 */

import { Card, Loader, Select, Table, Text } from '@mantine/core'
import { BarChart } from '@mantine/charts'
import {
  IconAlertTriangle,
  IconArrowDown,
  IconArrowUp,
  IconCoin,
  IconUsers,
} from '@tabler/icons-react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { dashboard, reference } from '../api/resources'
import type { BandSlice, CountrySlice, CurrencySlice } from '../api/types'
import { StatCard } from '../components/StatCard'
import { formatLocal, formatUsd, formatUsdAxis } from '../format/money'
import styles from './DashboardPage.module.css'

/**
 * What every chart on this page shares.
 *
 * `barCategoryGap` is the fix for bars drawn shoulder to shoulder: Recharts defaults
 * to 10%, which at nine countries in a half-width card reads as one solid block
 * rather than nine values. `maxBarWidth` stops the opposite problem - seven bands in
 * the same space would otherwise render as slabs.
 */
const CHART = {
  h: 'var(--panel-chart-height)',
  maxBarWidth: 34,
  barChartProps: { barCategoryGap: '28%' },
  gridAxis: 'y' as const,
  withLegend: false,
  tickLine: 'none' as const,
}

/** Axis ticks as a scale. The table under each chart carries the exact figure. */
const USD_AXIS = { yAxisProps: { tickFormatter: formatUsdAxis, width: 56 } }

export function DashboardPage() {
  const [country, setCountry] = useState<string | null>(null)
  const [department, setDepartment] = useState<string | null>(null)
  const [band, setBand] = useState<string | null>(null)

  const { data: refData } = useQuery({
    queryKey: ['reference'],
    queryFn: reference.all,
    staleTime: Infinity,
  })

  const filters = {
    country,
    department: department ? Number(department) : null,
    band: band ? Number(band) : null,
  }

  const { data, isPending, isError } = useQuery({
    queryKey: ['dashboard', filters],
    queryFn: () => dashboard.summary(filters),
    placeholderData: keepPreviousData,
  })

  if (isError) {
    return (
      <div className="u-page">
        <div className="u-empty" role="alert">
          <IconAlertTriangle size={28} stroke={1.5} />
          {/* An empty dashboard after a failed request reads as "the company has no
              payroll" - alarming, and false. */}
          <Text fw={500}>Could not load the dashboard</Text>
          <Text size="sm">The server did not answer. Try again in a moment.</Text>
        </div>
      </div>
    )
  }

  if (isPending || !data) {
    return (
      <div className="u-page">
        <Loader size="sm" />
      </div>
    )
  }

  const empty = data.employee_count === 0

  return (
    <div className="u-page u-stack">
      <div className="u-row-between">
        <div className="u-stack-tight">
          <h1 className="u-title">Dashboard</h1>
          <span className="u-muted">
            Every figure in USD, converted at the rate in force when each salary was
            recorded.
          </span>
        </div>
        <div className={styles.filters}>
          <Select
            aria-label="Country"
            placeholder="All countries"
            clearable
            searchable
            nothingFoundMessage="No match"
            value={country}
            onChange={setCountry}
            data={(refData?.countries ?? []).map((c) => ({ value: c.code, label: c.name }))}
            w={180}
          />
          <Select
            aria-label="Department"
            placeholder="All departments"
            clearable
            searchable
            nothingFoundMessage="No match"
            value={department}
            onChange={setDepartment}
            data={(refData?.departments ?? []).map((d) => ({
              value: String(d.id),
              label: d.name,
            }))}
            w={180}
          />
          <Select
            aria-label="Band"
            placeholder="All bands"
            clearable
            searchable
            nothingFoundMessage="No match"
            value={band}
            onChange={setBand}
            data={(refData?.bands ?? []).map((b) => ({ value: String(b.id), label: b.name }))}
            w={140}
          />
        </div>
      </div>

      {empty ? (
        <Card>
          <div className="u-empty">
            <Text fw={500}>No salary data yet</Text>
            <Text size="sm">
              Nobody matching these filters has a current salary. Import a file or set a
              salary to see figures here.
            </Text>
          </div>
        </Card>
      ) : (
        <>
          <div className={styles.stats}>
            <StatCard
              label="People on payroll"
              value={data.employee_count.toLocaleString()}
              hint="With a current salary"
              icon={<IconUsers size={18} stroke={1.6} color="var(--c-text-faint)" />}
            />
            <StatCard
              label="Total payroll"
              value={formatUsd(data.total_payroll_usd)}
              unit="USD"
              icon={<IconCoin size={18} stroke={1.6} color="var(--c-text-faint)" />}
            />
            <StatCard label="Average salary" value={formatUsd(data.average_salary_usd)} unit="USD" />
            <StatCard
              label="Median salary"
              value={formatUsd(data.median_salary_usd)}
              unit="USD"
              // Shown next to the mean because they differ: one outlier moves the
              // mean and leaves the median alone.
              hint="Half earn more, half earn less"
            />
          </div>

          <div className={styles.panels}>
            {data.highest && (
              <StatCard
                label="Highest paid"
                value={formatUsd(data.highest.usd_amount)}
                unit="USD"
                icon={<IconArrowUp size={18} stroke={1.6} color="var(--c-positive)" />}
                hint={`${data.highest.first_name} ${data.highest.last_name} · ${formatLocal(
                  data.highest.amount,
                  data.highest.currency,
                )}`}
              />
            )}
            {data.lowest && (
              <StatCard
                label="Lowest paid"
                value={formatUsd(data.lowest.usd_amount)}
                unit="USD"
                icon={<IconArrowDown size={18} stroke={1.6} color="var(--c-text-faint)" />}
                hint={`${data.lowest.first_name} ${data.lowest.last_name} · ${formatLocal(
                  data.lowest.amount,
                  data.lowest.currency,
                )}`}
              />
            )}
          </div>

          <div className={styles.panels}>
            <Card className={styles.panel}>
              <span className="u-label">Payroll by country (USD)</span>
              <BarChart
                {...CHART}
                {...USD_AXIS}
                className={styles.chart}
                data={data.by_country.map((row) => ({
                  country: row.name,
                  total: Number(row.total_usd),
                }))}
                dataKey="country"
                series={[{ name: 'total', color: 'brand.6', label: 'Total USD' }]}
                valueFormatter={(value) => formatUsd(String(value))}
              />
              <div className={styles.panelBody}>
                <CountryTable rows={data.by_country} />
              </div>
            </Card>

            <Card className={styles.panel}>
              <span className="u-label">Average salary by band (USD)</span>
              <BarChart
                {...CHART}
                {...USD_AXIS}
                className={styles.chart}
                // Already ordered by rank from the API. Not re-sorted here: by name
                // bands read L1, L10, L11, L2, and that chart means nothing.
                data={data.by_band.map((row) => ({
                  band: row.name,
                  average: Number(row.average_usd),
                }))}
                dataKey="band"
                series={[{ name: 'average', color: 'positive.6', label: 'Average USD' }]}
                valueFormatter={(value) => formatUsd(String(value))}
              />
              <div className={styles.panelBody}>
                <BandTable rows={data.by_band} />
              </div>
            </Card>

            <Card className={styles.panel}>
              <span className="u-label">Headcount by department</span>
              <BarChart
                {...CHART}
                className={styles.chart}
                data={data.by_department.map((row) => ({
                  department: row.name,
                  people: row.employee_count,
                }))}
                dataKey="department"
                series={[{ name: 'people', color: 'brand.4', label: 'People' }]}
              />
            </Card>

            <Card className={styles.panel}>
              <span className="u-label">What payroll is paid in</span>
              <div className={styles.panelBody}>
                <CurrencyTable rows={data.currency_breakdown} />
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

function CountryTable({ rows }: { rows: CountrySlice[] }) {
  return (
    <Table mt="md" aria-label="Payroll by country">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Country</Table.Th>
          <Table.Th>People</Table.Th>
          <Table.Th ta="right">Total USD</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => (
          <Table.Tr key={row.country_code}>
            <Table.Td>{row.name}</Table.Td>
            <Table.Td>{row.employee_count}</Table.Td>
            <Table.Td ta="right">
              <span className="u-money">{formatUsd(row.total_usd)}</span>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}

function BandTable({ rows }: { rows: BandSlice[] }) {
  return (
    <Table mt="md" aria-label="Average salary by band">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Band</Table.Th>
          <Table.Th>People</Table.Th>
          <Table.Th ta="right">Average USD</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => (
          <Table.Tr key={row.band_id}>
            <Table.Td>{row.name}</Table.Td>
            <Table.Td>{row.employee_count}</Table.Td>
            <Table.Td ta="right">
              <span className="u-money">{formatUsd(row.average_usd)}</span>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}

function CurrencyTable({ rows }: { rows: CurrencySlice[] }) {
  return (
    <Table mt="md" aria-label="Payroll by currency">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Currency</Table.Th>
          <Table.Th>People</Table.Th>
          <Table.Th ta="right">Local total</Table.Th>
          <Table.Th ta="right">USD</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => (
          <Table.Tr key={row.currency}>
            <Table.Td>{row.currency}</Table.Td>
            <Table.Td>{row.employee_count}</Table.Td>
            <Table.Td ta="right">
              <span className="u-money-secondary">
                {formatLocal(row.total_local, row.currency)}
              </span>
            </Table.Td>
            <Table.Td ta="right">
              <span className="u-money">{formatUsd(row.total_usd)}</span>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}
