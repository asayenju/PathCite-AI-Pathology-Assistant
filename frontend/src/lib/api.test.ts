import { afterEach, describe, expect, it, vi } from 'vitest'
import { PredictError, predict, validateFile } from './api'

const png = () => new File([new Uint8Array([1, 2, 3])], 'a.png', { type: 'image/png' })

function mockFetch(impl: () => Promise<Response>) {
  vi.stubGlobal('fetch', vi.fn(impl))
}

afterEach(() => vi.unstubAllGlobals())

describe('validateFile', () => {
  it('accepts png, jpeg and tiff', () => {
    for (const type of ['image/png', 'image/jpeg', 'image/tiff']) {
      expect(validateFile(new File([new Uint8Array(1)], 'x', { type }))).toBeNull()
    }
  })

  it('rejects other types, empty files and files over 10 MB', () => {
    expect(validateFile(new File(['hi'], 'a.txt', { type: 'text/plain' }))?.code).toBe('unsupported_type')
    expect(validateFile(new File([], 'a.png', { type: 'image/png' }))?.code).toBe('empty_file')
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'a.png', { type: 'image/png' })
    expect(validateFile(big)?.code).toBe('file_too_large')
  })
})

describe('predict', () => {
  it('returns the parsed response on success', async () => {
    const body = { label: 8, tissue: 'TUM', confidence: 0.9 }
    mockFetch(async () => new Response(JSON.stringify(body), { status: 200 }))
    expect(await predict(png())).toEqual(body)
  })

  it('posts multipart to /api/predict with a file field', async () => {
    mockFetch(async () => new Response('{}', { status: 200 }))
    await predict(png())
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/predict')
    expect((init.body as FormData).get('file')).toBeInstanceOf(File)
  })

  it.each([
    [413, 'file_too_large', /10 MB/],
    [415, 'unsupported_type', /PNG, JPEG and TIFF/],
    [422, 'corrupt_image', /couldn't be read/],
    [503, 'model_unavailable', /not loaded/],
  ])('maps backend %i %s to a readable message', async (status, code, pattern) => {
    mockFetch(async () => new Response(JSON.stringify({ detail: 'raw', code }), { status }))
    const error = await predict(png()).catch((e) => e)
    expect(error).toBeInstanceOf(PredictError)
    expect(error.code).toBe(code)
    expect(error.message).toMatch(pattern)
  })

  it('reports an unreachable backend when fetch itself fails', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const error = await predict(png()).catch((e) => e)
    expect(error.code).toBe('network')
    expect(error.message).toMatch(/uvicorn/)
  })

  it('treats a non-JSON 5xx (dev proxy with backend down) as unreachable', async () => {
    mockFetch(async () => new Response('proxy error', { status: 500 }))
    expect((await predict(png()).catch((e) => e)).code).toBe('network')
  })

  it('does not call the server for an invalid file', async () => {
    mockFetch(async () => new Response('{}'))
    const error = await predict(new File(['x'], 'a.txt', { type: 'text/plain' })).catch((e) => e)
    expect(error.code).toBe('unsupported_type')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('rethrows aborts untouched', async () => {
    mockFetch(async () => {
      throw new DOMException('aborted', 'AbortError')
    })
    const error = await predict(png()).catch((e) => e)
    expect(error.name).toBe('AbortError')
  })
})
