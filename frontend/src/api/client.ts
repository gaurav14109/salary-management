/**
 * The single place the browser talks to the BFF.
 *
 * It holds no token. The session is an httpOnly cookie the browser attaches and this
 * code cannot read - which is the point: a successful XSS here finds no credential to
 * steal, only a session it can use while the page is open.
 */

import { DEFAULT_LOGIN_URL, notifySessionExpired } from './session'

export class ApiError extends Error {
  readonly status: number
  readonly fieldErrors: Record<string, string>
  /** Present on a 401: where to send the browser to authenticate. */
  readonly loginUrl?: string

  constructor(
    status: number,
    message: string,
    fieldErrors: Record<string, string> = {},
    loginUrl?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
    this.loginUrl = loginUrl
  }
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const CSRF_COOKIE = 'salary_csrf'

/**
 * Read the CSRF cookie the BFF set. Deliberately readable: the double-submit pair
 * works because a cross-site page can cause the cookie to be *sent* but cannot *read*
 * it to build the header.
 */
export function csrfToken(): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : null
}

type ValidationItem = { loc: (string | number)[]; msg: string }
type FieldItem = { field: string; message: string }

function isValidationDetail(detail: unknown): detail is ValidationItem[] {
  return Array.isArray(detail) && detail.every((i) => i && typeof i === 'object' && 'loc' in i)
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {}
  try {
    body = await response.json()
  } catch {
    // A proxy error or a crash returns HTML or nothing.
  }

  const loginUrl = typeof body.login_url === 'string' ? body.login_url : undefined
  const detail = body.detail

  // The BFF's own validation shape: [{field, message}]
  if (Array.isArray(body.fields)) {
    const fieldErrors = Object.fromEntries(
      (body.fields as FieldItem[]).map((f) => [f.field, f.message]),
    )
    return new ApiError(response.status, 'Some fields need attention', fieldErrors, loginUrl)
  }
  // FastAPI's raw 422 shape, if an upstream passes one straight through.
  if (isValidationDetail(detail)) {
    const fieldErrors = Object.fromEntries(
      detail.map((d) => [String(d.loc[d.loc.length - 1]), d.msg]),
    )
    return new ApiError(response.status, 'Some fields need attention', fieldErrors, loginUrl)
  }
  if (typeof detail === 'string') {
    return new ApiError(response.status, detail, {}, loginUrl)
  }
  return new ApiError(
    response.status,
    response.statusText || `Request failed (${response.status})`,
    {},
    loginUrl,
  )
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  // FormData carries its own Content-Type, including a boundary only the browser
  // knows. Setting the header by hand produces a body the server cannot parse, and
  // the error it returns says nothing about why.
  const isForm = body instanceof FormData
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json'

  if (UNSAFE_METHODS.has(method)) {
    const token = csrfToken()
    // Sent even when absent, so the BFF's 403 is the single place this is enforced
    // rather than the client deciding whether the rule applies.
    if (token) headers['X-CSRF-Token'] = token
  }

  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      // Without this the session cookie is not sent and every call is anonymous.
      credentials: 'include',
      // The BFF answers 401 with login_url instead of redirecting, precisely so this
      // never happens: a fetch following a redirect to Keycloak fails CORS and
      // surfaces as an unrelated-looking error.
      redirect: 'manual',
    })
  } catch {
    throw new ApiError(0, 'Could not reach the server')
  }

  if (!response.ok) {
    const error = await toApiError(response)
    // A 401 from *any* call means the session is gone, not just from /auth/me.
    // Announced here, at the one place every request passes through, so no caller
    // has to remember to handle it - and a page added later inherits the behaviour.
    // Only 401: a 403 is a role problem, and bouncing someone to Keycloak for
    // trying an admin action would log them out for doing nothing wrong and return
    // them to the same refusal.
    if (response.status === 401) notifySessionExpired(error.loginUrl ?? DEFAULT_LOGIN_URL)
    throw error
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T,>(path: string) => request<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T,>(path: string, body?: unknown) => request<T>('PUT', path, body ?? {}),
  /**
   * Partial update. The body carries only the fields being changed - a PUT would
   * send the whole record back and silently overwrite whatever moved since the form
   * was loaded.
   */
  patch: <T,>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T,>(path: string) => request<T>('DELETE', path),
  /**
   * Upload a file. Same session, same CSRF rule, same error normalisation as every
   * other call - an upload is a mutation like any other.
   */
  upload: <T,>(path: string, file: File, field = 'file') => {
    const form = new FormData()
    form.append(field, file)
    return request<T>('POST', path, form)
  },
}
