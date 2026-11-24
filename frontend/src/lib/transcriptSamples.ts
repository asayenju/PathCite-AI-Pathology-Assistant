import { sampleUrl } from '@/lib/slide'
import type { TranscriptManifest, TranscriptSample, TranscriptSampleFolder } from '@/types'

/** Drag data type that marks a drag as one of the bundled sample transcripts. Payload: the item's file path. */
export const TRANSCRIPT_MIME = 'application/x-pathcite-transcript'

export const MANIFEST_PATH = 'transcripts/index.json'

/** Shown when a sample's JSON can't be fetched. */
export const SAMPLE_LOAD_ERROR = "Couldn't load the sample transcript. Try again."

/** Badge text per manifest vendor. Unknown vendors show their raw id. */
export const VENDOR_NAMES: Record<string, string> = {
  deepgram: 'Deepgram',
  aws_medical: 'AWS Medical',
  whisperx: 'WhisperX',
  openai_diarized: 'OpenAI',
}

export function vendorName(vendor: string): string {
  return VENDOR_NAMES[vendor] ?? vendor
}

const isString = (value: unknown): value is string => typeof value === 'string' && value.length > 0

/** Only same-origin JSON files under public/transcripts/ are fetched. */
const isSafePath = (file: string) => file.startsWith('transcripts/') && file.endsWith('.json') && !file.includes('..')

function isItem(value: unknown): value is TranscriptSample {
  const item = value as Partial<TranscriptSample> | null
  return (
    !!item &&
    isString(item.id) &&
    isString(item.title) &&
    isString(item.vendor) &&
    isString(item.file) &&
    isSafePath(item.file)
  )
}

function isFolder(value: unknown): value is TranscriptSampleFolder {
  const folder = value as Partial<TranscriptSampleFolder> | null
  return (
    !!folder &&
    isString(folder.name) &&
    isString(folder.slug) &&
    Array.isArray(folder.items) &&
    folder.items.every(isItem)
  )
}

/** The manifest if it has the expected shape, else null. */
export function parseTranscriptManifest(value: unknown): TranscriptManifest | null {
  const manifest = value as Partial<TranscriptManifest> | null
  if (!manifest || !isString(manifest.notice) || !Array.isArray(manifest.folders)) return null
  if (!manifest.folders.every(isFolder)) return null
  const folders = manifest.folders.filter((folder) => folder.items.length > 0)
  return folders.length ? { notice: manifest.notice, folders } : null
}

/** Fetch and check the bundled sample list. Any failure gives null, and the samples panel stays hidden. */
export async function loadTranscriptManifest(): Promise<TranscriptManifest | null> {
  try {
    const response = await fetch(sampleUrl(MANIFEST_PATH))
    if (!response.ok) return null
    return parseTranscriptManifest(await response.json())
  } catch {
    return null
  }
}

export function findSample(manifest: TranscriptManifest | null, file: string): TranscriptSample | null {
  for (const folder of manifest?.folders ?? []) {
    const item = folder.items.find((s) => s.file === file)
    if (item) return item
  }
  return null
}
