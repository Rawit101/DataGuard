import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, post } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('API requests', () => {
  it('sends JSON requests to the same-origin API', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'p1'}), {status:200}))
    vi.stubGlobal('fetch', fetchMock)
    expect(await post<{id:string}>('/projects', {name:'Test'})).toEqual({id:'p1'})
    expect(fetchMock.mock.calls[0][0]).toBe('/api/projects')
    expect(fetchMock.mock.calls[0][1].headers['Content-Type']).toBe('application/json')
  })

  it('lets the browser set multipart boundaries and exposes useful errors', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('{}', {status:200}))
      .mockResolvedValueOnce(new Response('Dataset is not approved', {status:400}))
    vi.stubGlobal('fetch', fetchMock)
    await api('/imports/inspect', {method:'POST',body:new FormData()})
    expect(fetchMock.mock.calls[0][1].headers).toBeUndefined()
    await expect(api('/queries/run')).rejects.toThrow('Dataset is not approved')
  })
})
