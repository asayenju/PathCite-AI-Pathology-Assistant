import { afterEach, describe, expect, it, vi } from 'vitest'
import { MESSAGE_CODES, PredictError, messageFor } from './api'
import {
  MAX_TRANSCRIPT_BYTES,
  TRANSCRIPT_ERROR_CODES,
  importTranscript,
  transcriptMessageFor,
  validateTranscriptFile,
} from './transcripts'
import type { TranscriptImport } from '@/types'

const json = (name = 'visit.json', type = 'application/json') => new File(['{"results":{}}'], name, { type })

const imported: TranscriptImport = {
  source: 'deepgram',
  language: 'en',
  duration_s: 61.5,
  speakers: [
    { id: 'S1', source_label: '0', word_count: 10, talk_time_s: 30, preview: ['Hello'], suggested_role: 'doctor' },
  ],
  turns: [],
  warnings: [],
  notice: 'Research use only.',
}

function mockFetch(impl: () => Promise<Response>) {
  vi.stubGlobal('fetch', vi.fn(impl))
}

afterEach(() => vi.unstubAllGlobals())

describe('validateTranscriptFile', () => {
  it('accepts a .json name or an application/json type', () => {
    expect(validateTranscriptFile(json('a.json', ''))).toBeNull()
    expect(validateTranscriptFile(json('A.JSON', 'text/plain'))).toBeNull()
    expect(validateTranscriptFile(json('export', 'application/json'))).toBeNull()
  })

  it('refuses empty, oversized and non-JSON files with transcript wording', () => {
    expect(validateTranscriptFile(new File([], 'a.json'))?.code).toBe('empty_file')

    const big = new File([new Uint8Array(MAX_TRANSCRIPT_BYTES + 1)], 'a.json', { type: 'application/json' })
    const tooBig = validateTranscriptFile(big)
    expect(tooBig?.code).toBe('file_too_large')
    expect(tooBig?.message).toMatch(/20 MB/)

    const text = validateTranscriptFile(new File(['hi'], 'notes.txt', { type: 'text/plain' }))
    expect(text?.code).toBe('unsupported_transcript_format')
    expect(text?.message).toBe(transcriptMessageFor('unsupported_transcript_format'))
  })

  it('accepts exactly 20 MB', () => {
    const edge = new File([new Uint8Array(MAX_TRANSCRIPT_BYTES)], 'a.json')
    expect(validateTranscriptFile(edge)).toBeNull()
  })
})

describe('messages', () => {
  it('has a specific message for every transcript error code', () => {
    const generic = messageFor('__no_such_code__')
    for (const code of TRANSCRIPT_ERROR_CODES) {
      expect(transcriptMessageFor(code), code).not.toBe(generic)
    }
  })

  it('adds every new backend code to the shared messageFor', () => {
    const generic = messageFor('__no_such_code__')
    for (const code of ['invalid_json', 'unsupported_transcript_format', 'undiarized_transcript', 'empty_transcript']) {
      expect(MESSAGE_CODES).toContain(code)
      expect(messageFor(code), code).not.toBe(generic)
    }
  })

  it('keeps the image size message at 10 MB and uses 20 MB for transcripts', () => {
    expect(messageFor('file_too_large')).toMatch(/10 MB/)
    expect(transcriptMessageFor('file_too_large')).toMatch(/20 MB/)
  })

  it('reuses the shared network message', () => {
    expect(transcriptMessageFor('network')).toBe(messageFor('network'))
  })

  it('tells the user how to fix an undiarized transcript', () => {
    expect(transcriptMessageFor('undiarized_transcript')).toMatch(/speaker diarization/)
  })
})

describe('importTranscript', () => {
  it('posts multipart to /api/transcripts/import and returns the parsed response', async () => {
    mockFetch(async () => new Response(JSON.stringify(imported), { status: 200 }))
    expect(await importTranscript(json())).toEqual(imported)
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]!
    expect(url).toBe('/api/transcripts/import')
    expect(init.method).toBe('POST')
    expect((init.body as FormData).get('file')).toBeInstanceOf(File)
  })

  const statusFor: Record<string, number> = { file_too_large: 413, internal_error: 500, network: 503 }

  it.each(TRANSCRIPT_ERROR_CODES.filter((c) => c !== 'network'))(
    'maps backend code %s to its transcript message',
    async (code) => {
      const status = statusFor[code] ?? 422
      mockFetch(async () => new Response(JSON.stringify({ detail: 'raw detail', code }), { status }))
      const error = await importTranscript(json()).catch((e) => e)
      expect(error).toBeInstanceOf(PredictError)
      expect(error.code).toBe(code)
      expect(error.message).toBe(transcriptMessageFor(code))
      expect(error.message).not.toBe('raw detail')
    },
  )

  it('reports an unreachable backend when fetch itself fails', async () => {
    mockFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const error = await importTranscript(json()).catch((e) => e)
    expect(error.code).toBe('network')
    expect(error.message).toBe(messageFor('network'))
  })

  it('treats a non-JSON 5xx as unreachable', async () => {
    mockFetch(async () => new Response('proxy error', { status: 502 }))
    expect((await importTranscript(json()).catch((e) => e)).code).toBe('network')
  })

  it('does not call the server for a non-JSON file', async () => {
    mockFetch(async () => new Response('{}'))
    const error = await importTranscript(new File(['x'], 'a.txt', { type: 'text/plain' })).catch((e) => e)
    expect(error.code).toBe('unsupported_transcript_format')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('passes the signal and rethrows aborts untouched', async () => {
    mockFetch(async () => {
      throw new DOMException('aborted', 'AbortError')
    })
    const controller = new AbortController()
    const error = await importTranscript(json(), controller.signal).catch((e) => e)
    expect(error.name).toBe('AbortError')
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls[0]![1].signal).toBe(controller.signal)
  })
})
