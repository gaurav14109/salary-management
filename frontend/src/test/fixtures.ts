/**
 * Sample payloads, shaped exactly like the API's.
 *
 * Money is a string here for the same reason it is a string in `types.ts`: a fixture
 * that used numbers would let a component pass its tests and then render a rounded
 * salary against the real API.
 */

import type {
  Dashboard,
  ImportJob,
  Employee,
  EmployeeListItem,
  Page,
  ReferenceData,
  Salary,
} from '../api/types'

export function anEmployee(overrides: Partial<Employee> = {}): Employee {
  return {
    id: 1,
    employee_code: 'EMP-1042',
    first_name: 'Priya',
    last_name: 'Sharma',
    email: 'priya@acme.test',
    country_code: 'IN',
    department_id: 1,
    job_title: 'Engineer',
    band_id: 3,
    status: 'ACTIVE',
    hire_date: '2024-04-01',
    termination_date: null,
    ...overrides,
  }
}

export function aSalary(overrides: Partial<Salary> = {}): Salary {
  return {
    id: 10,
    amount: '1200000.000',
    currency: 'INR',
    usd_amount: '14379.87',
    fx_units_per_usd: '83.45000000',
    fx_rate_date: '2026-01-01',
    effective_from: '2026-04-01',
    effective_to: null,
    change_reason: 'MERIT',
    created_by: 1,
    created_by_name: 'rahul',
    created_at: '2026-04-01T09:30:00Z',
    ...overrides,
  }
}

export function aListPage(
  items: EmployeeListItem[] = [{ employee: anEmployee(), current_salary: aSalary() }],
  overrides: Partial<Page<EmployeeListItem>> = {},
): Page<EmployeeListItem> {
  return { items, total: items.length, page: 1, size: 50, ...overrides }
}

export function referenceData(): ReferenceData {
  return {
    countries: [
      { code: 'IN', name: 'India' },
      { code: 'DE', name: 'Germany' },
    ],
    currencies: [
      { code: 'USD', name: 'US Dollar', minor_units: 2 },
      { code: 'INR', name: 'Indian Rupee', minor_units: 2 },
    ],
    departments: [
      { id: 1, name: 'Engineering' },
      { id: 2, name: 'Design' },
    ],
    bands: [
      { id: 3, name: 'L3', rank: 3 },
      { id: 5, name: 'L5', rank: 5 },
    ],
  }
}

/**
 * A dashboard with hand-checkable numbers.
 *
 * Four people: two countries, two bands, two currencies. Small enough that the
 * totals can be verified by reading them, which is the point - an aggregate fixture
 * whose expected values came out of the code proves nothing.
 */
export function aDashboard(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    employee_count: 4,
    total_payroll_usd: '180017.97',
    average_salary_usd: '45004.49',
    median_salary_usd: '35005.99',
    highest: {
      employee_code: 'EMP-D4',
      first_name: 'Lukas',
      last_name: 'Weber',
      usd_amount: '100000.00',
      amount: '92000.000',
      currency: 'EUR',
    },
    lowest: {
      employee_code: 'EMP-D1',
      first_name: 'Priya',
      last_name: 'Sharma',
      usd_amount: '10005.99',
      amount: '835000.000',
      currency: 'INR',
    },
    by_country: [
      { country_code: 'DE', name: 'Germany', employee_count: 2, total_usd: '150000.00', average_usd: '75000.00' },
      { country_code: 'IN', name: 'India', employee_count: 2, total_usd: '30017.97', average_usd: '15008.99' },
    ],
    by_department: [
      { department_id: 2, name: 'Design', employee_count: 2, total_usd: '150000.00', average_usd: '75000.00' },
      { department_id: 1, name: 'Engineering', employee_count: 2, total_usd: '30017.97', average_usd: '15008.99' },
    ],
    by_band: [
      { band_id: 3, name: 'L3', rank: 3, employee_count: 2, total_usd: '60005.99', average_usd: '30003.00' },
      { band_id: 5, name: 'L5', rank: 5, employee_count: 1, total_usd: '20011.98', average_usd: '20011.98' },
      { band_id: 7, name: 'L7', rank: 7, employee_count: 1, total_usd: '100000.00', average_usd: '100000.00' },
    ],
    currency_breakdown: [
      { currency: 'EUR', employee_count: 2, total_local: '138000.000', total_usd: '150000.00' },
      { currency: 'INR', employee_count: 2, total_local: '2505000.000', total_usd: '30017.97' },
    ],
    ...overrides,
  }
}

/** Day one: nothing imported yet. The dashboard still has to render. */
export function anEmptyDashboard(): Dashboard {
  return {
    employee_count: 0,
    total_payroll_usd: '0.00',
    average_salary_usd: '0.00',
    median_salary_usd: '0.00',
    highest: null,
    lowest: null,
    by_country: [],
    by_department: [],
    by_band: [],
    currency_breakdown: [],
  }
}

export function anImportJob(overrides: Partial<ImportJob> = {}): ImportJob {
  return {
    id: 7,
    filename: 'people.csv',
    status: 'SUCCESS',
    failure_reason: null,
    started_at: '2026-11-08T10:00:00Z',
    finished_at: '2026-11-08T10:00:05Z',
    total_rows: 3,
    success_rows: 3,
    error_rows: 0,
    employees_created: 2,
    employees_updated: 1,
    salaries_created: 2,
    salaries_unchanged: 1,
    errors: [],
    created_by: 1,
    created_at: '2026-11-07T10:00:00Z',
    ...overrides,
  }
}

/** A rejected file: nothing was committed, and every bad row is named. */
export function aFailedImportJob(overrides: Partial<ImportJob> = {}): ImportJob {
  return anImportJob({
    status: 'FAILED',
    failure_reason: null,
    started_at: '2026-11-08T10:00:00Z',
    finished_at: '2026-11-08T10:00:05Z',
    success_rows: 0,
    error_rows: 2,
    employees_created: 0,
    employees_updated: 0,
    salaries_created: 0,
    salaries_unchanged: 0,
    errors: [
      { row_number: 2, field: 'currency', message: 'Unknown currency: XYZ.' },
      { row_number: 5, field: 'amount', message: 'Must be positive, got -5.' },
    ],
    ...overrides,
  })
}
