/**
 * The shapes the BFF returns, mirroring the backend's Pydantic models.
 *
 * Money is `string`, not `number`, everywhere. A JSON number is an IEEE 754 double:
 * parsing "1200000.000" into one and rendering it back gives a different figure, and
 * nobody notices until Finance does. These strings are formatted for display and
 * never arithmetic - any sum the UI appears to need is a sum the API already
 * computed, in SQL, from the frozen column.
 */

export type Page<T> = {
  items: T[]
  /** Rows matching the filter, not rows on this page. */
  total: number
  page: number
  size: number
}

export type ApiFieldError = { field: string; message: string }

export type Salary = {
  id: number
  /** What the person is actually paid, in their currency. */
  amount: string
  currency: string
  /** The same pay in USD, frozen at write time with the rate below. */
  usd_amount: string
  fx_units_per_usd: string
  fx_rate_date: string
  /** Inclusive. The day this pay starts applying. */
  effective_from: string
  /** Inclusive. Null means this is the current row. */
  effective_to: string | null
  change_reason: string | null
  created_by: number
  /** Who made the change. Null only for rows written before names were captured. */
  created_by_name: string | null
  /** When the change was made - distinct from effective_from, when it starts. */
  created_at: string
}

export type Employee = {
  id: number
  employee_code: string
  first_name: string
  last_name: string
  email: string | null
  country_code: string
  department_id: number
  job_title: string
  band_id: number | null
  status: 'ACTIVE' | 'INACTIVE'
  hire_date: string
  termination_date: string | null
}

/** A list row: the person, and what they are currently paid. Null is a new hire. */
export type EmployeeListItem = { employee: Employee; current_salary: Salary | null }

export type EmployeeDetail = {
  employee: Employee
  current_salary: Salary | null
  /** Newest first - the order a timeline reads. */
  history: Salary[]
}

export type SalaryChangeResult = {
  /** False when the pay already matched: no row written, no audit entry. */
  changed: boolean
  salary: Salary
}

export type Country = { code: string; name: string }
export type Currency = { code: string; name: string; minor_units: number }
export type Department = { id: number; name: string }
/** `rank` is the sort key: by name, bands read L1, L10, L11, L2. */
export type Band = { id: number; name: string; rank: number }

export type ReferenceData = {
  countries: Country[]
  currencies: Currency[]
  departments: Department[]
  bands: Band[]
}

export type ImportRowError = {
  /** Spreadsheet line, header counted. 0 means the whole file. */
  row_number: number
  field: string
  message: string
}

export type ImportJob = {
  id: number
  filename: string
  status: 'PENDING' | 'SUCCESS' | 'FAILED'
  total_rows: number
  success_rows: number
  error_rows: number
  employees_created: number
  employees_updated: number
  salaries_created: number
  /** Rows whose pay already matched. Why re-uploading an export is safe. */
  salaries_unchanged: number
  errors: ImportRowError[]
  created_by: number
  created_at: string
}

export type TopEarner = {
  employee_code: string
  first_name: string
  last_name: string
  usd_amount: string
  amount: string
  currency: string
}

export type CountrySlice = {
  country_code: string
  name: string
  employee_count: number
  total_usd: string
  average_usd: string
}

export type DepartmentSlice = {
  department_id: number
  name: string
  employee_count: number
  total_usd: string
  average_usd: string
}

export type BandSlice = {
  band_id: number
  name: string
  rank: number
  employee_count: number
  total_usd: string
  average_usd: string
}

export type CurrencySlice = {
  currency: string
  employee_count: number
  total_local: string
  total_usd: string
}

export type Dashboard = {
  /** People with a current salary, not headcount. */
  employee_count: number
  total_payroll_usd: string
  average_salary_usd: string
  /** Reported beside the mean because they differ: an outlier moves one, not both. */
  median_salary_usd: string
  highest: TopEarner | null
  lowest: TopEarner | null
  by_country: CountrySlice[]
  by_department: DepartmentSlice[]
  by_band: BandSlice[]
  currency_breakdown: CurrencySlice[]
}

export type FxRate = {
  id: number
  currency: string
  units_per_usd: string
  as_of: string
  created_by: number
}

/** One line of an uploaded file, still as text. */
export type ImportPreviewRow = {
  /** Spreadsheet line, header counted - what the grid shows in its margin. */
  row_number: number
  values: Record<string, string>
}

/**
 * What a file would do, without doing it.
 *
 * Values stay strings all the way through. The grid edits a spreadsheet, and a date
 * typed as `01/04/2024` has to render exactly as typed or the user cannot see what
 * is wrong with it.
 */
export type ImportPreview = {
  columns: string[]
  rows: ImportPreviewRow[]
  errors: ImportRowError[]
  /** Rows in the file, before truncation - not `rows.length`. */
  row_count: number
  error_count: number
  /** The file has more rows than the grid shows. Editing is off in that case. */
  truncated: boolean
}
