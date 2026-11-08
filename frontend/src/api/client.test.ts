import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, csrfToken } from './client'

function mockResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  document.cookie = 'salary_csrf=; Max-Age=0'
})

describe('api client', () => {
  it('returns the parsed body on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(200, { status: 'ok' })))

    await expect(api.get('/api/health')).resolves.toEqual({ status: 'ok' })
  })

  it('always sends the session cookie', async () => {
    /* Without credentials: include the cookie is not sent and every call is anonymous. */
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/api/health')

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'include' })
  })

  it('does not follow redirects', async () => {
    /* A fetch following a redirect to Keycloak fails CORS and surfaces as an
       unrelated-looking error, which is why the BFF answers 401 instead. */
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/api/health')

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ redirect: 'manual' })
  })

  it('attaches the CSRF header to a write', async () => {
    document.cookie = 'salary_csrf=token-abc'
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await api.put('/api/employees/42/salary', { amount: 1 })

    expect(fetchMock.mock.calls[0][1].headers['X-CSRF-Token']).toBe('token-abc')
  })

  it('does not attach the CSRF header to a read', async () => {
    document.cookie = 'salary_csrf=token-abc'
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/api/employees')

    expect(fetchMock.mock.calls[0][1].headers['X-CSRF-Token']).toBeUndefined()
  })

  it('reads the CSRF cookie the BFF set', () => {
    document.cookie = 'salary_csrf=abc%2Fdef'

    expect(csrfToken()).toBe('abc/def')
  })

  it('carries login_url off a 401 so the caller knows where to go', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(401, { detail: 'Not authenticated', login_url: '/auth/login' }),
      ),
    )

    const error = (await api.get('/api/employees').catch((e: unknown) => e)) as ApiError

    expect(error.status).toBe(401)
    expect(error.loginUrl).toBe('/auth/login')
  })

  it('throws an ApiError carrying the status on a non-2xx response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(403, { detail: 'Forbidden' })))

    await expect(api.get('/api/employees')).rejects.toMatchObject({
      status: 403,
      message: 'Forbidden',
    })
  })

  it("flattens the BFF's field errors", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(422, {
          detail: 'Invalid request',
          fields: [{ field: 'amount', message: 'must be greater than 0' }],
        }),
      ),
    )

    const error = (await api.post('/api/salaries', {}).catch((e: unknown) => e)) as ApiError

    expect(error.fieldErrors).toEqual({ amount: 'must be greater than 0' })
  })

  it('surfaces a network failure as an ApiError rather than a raw TypeError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const error = (await api.get('/api/health').catch((e: unknown) => e)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
  })
})

describe('patch', () => {
  it('sends PATCH with the CSRF header, like every other mutation', async () => {
    document.cookie = 'salary_csrf=tok-patch'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ job_title: 'Senior Engineer' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.patch('/api/employees/1', { job_title: 'Senior Engineer' })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('PATCH')
    expect(init.headers['X-CSRF-Token']).toBe('tok-patch')
    expect(init.credentials).toBe('include')
  })

  it('sends only the fields given, so a partial edit stays partial', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await api.patch('/api/employees/1', { job_title: 'X' })

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ job_title: 'X' })
  })
})

describe('upload', () => {
  it('sends the file as multipart without setting Content-Type itself', async () => {
    // The browser must set it, because only the browser knows the multipart boundary.
    // Setting it by hand produces a body the server cannot parse.
    document.cookie = 'salary_csrf=tok-upload'
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"id":1}', { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)

    await api.upload('/api/imports', new File(['a,b'], 'people.csv', { type: 'text/csv' }))

    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.headers['Content-Type']).toBeUndefined()
    expect(init.headers['X-CSRF-Token']).toBe('tok-upload')
    expect(init.credentials).toBe('include')
  })

  it('normalises a refusal the same way every other call does', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'That file is too large.' }), { status: 413 }),
      ),
    )

    await expect(api.upload('/api/imports', new File([''], 'big.csv'))).rejects.toMatchObject({
      status: 413,
      message: 'That file is too large.',
    })
  })
})
