/**
 * Every endpoint this UI calls, in one place.
 *
 * Components ask for `employees.list(filters)`, never a URL. One file knows the path
 * shapes, so a route that moves is one edit - and a component cannot quietly invent
 * an endpoint that does not exist.
 */

import { api } from './client'
import type {
  Dashboard,
  Employee,
  EmployeeDetail,
  EmployeeListItem,
  FxRate,
  ImportJob,
  ImportPreview,
  Page,
  ReferenceData,
  SalaryChangeResult,
} from './types'

const BASE = '/api'

export type EmployeeFilters = {
  q?: string
  country?: string | null
  department?: number | null
  band?: number | null
  status?: string | null
  sort?: string
  order?: 'asc' | 'desc'
  page?: number
  size?: number
}

/** Drops empty values rather than sending `country=` and filtering on nothing. */
function query(params: Record<string, unknown>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const employees = {
  list: (filters: EmployeeFilters = {}) =>
    api.get<Page<EmployeeListItem>>(`${BASE}/employees${query(filters)}`),

  detail: (id: number) => api.get<EmployeeDetail>(`${BASE}/employees/${id}`),

  create: (body: Record<string, unknown>) => api.post<Employee>(`${BASE}/employees`, body),

  update: (id: number, changes: Record<string, unknown>) =>
    api.patch<Employee>(`${BASE}/employees/${id}`, changes),

  setStatus: (id: number, body: { status: string; termination_date?: string | null }) =>
    api.put<Employee>(`${BASE}/employees/${id}/status`, body),

  setSalary: (id: number, body: Record<string, unknown>) =>
    api.put<SalaryChangeResult>(`${BASE}/employees/${id}/salary`, body),

  /**
   * A whole-window navigation, not a fetch. The browser has to receive the
   * Content-Disposition and save the file; a fetch would download it into memory and
   * leave the user looking at nothing.
   */
  exportUrl: (filters: EmployeeFilters = {}) => {
    const { page: _page, size: _size, ...rest } = filters
    return `${BASE}/employees/export${query(rest)}`
  },
}

export const reference = {
  all: () => api.get<ReferenceData>(`${BASE}/reference`),
}

export const dashboard = {
  summary: (filters: { country?: string | null; department?: number | null; band?: number | null } = {}) =>
    api.get<Dashboard>(`${BASE}/dashboard${query(filters)}`),
}

export const imports = {
  upload: (file: File) => api.upload<ImportJob>(`${BASE}/imports`, file),
  /** Parse and check a file without importing it. */
  preview: (file: File) => api.upload<ImportPreview>(`${BASE}/imports/preview`, file),
  list: (page = 1, size = 20) => api.get<Page<ImportJob>>(`${BASE}/imports${query({ page, size })}`),
  detail: (id: number) => api.get<ImportJob>(`${BASE}/imports/${id}`),
}

export const fxRates = {
  list: (currency?: string) => api.get<Page<FxRate>>(`${BASE}/fx-rates${query({ currency })}`),
  create: (body: { currency: string; units_per_usd: string; as_of: string }) =>
    api.post<FxRate>(`${BASE}/fx-rates`, body),
}
