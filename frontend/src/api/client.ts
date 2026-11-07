/**
 * The single place the browser talks to the BFF.
 *
 * It holds no token. The session is an httpOnly cookie the browser attaches and this
 * code cannot read - which is the point: a successful XSS here finds no credential to
 * steal, only a session it can use while the page is open.
 */

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
  if (body !== undefined) headers['Content-Type'] = 'application/json'

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
      body: body === undefined ? undefined : JSON.stringify(body),
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

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T,>(path: string) => request<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T,>(path: string, body?: unknown) => request<T>('PUT', path, body ?? {}),
  delete: <T,>(path: string) => request<T>('DELETE', path),
}
