/** Drag data type that marks a drag as one of the bundled slides (the stage looks for it). */
export const SLIDE_MIME = 'application/x-pathcite-slide'

/** Public URL of a bundled sample image. */
export function sampleUrl(file: string): string {
  return `${import.meta.env.BASE_URL}${file}`
}
