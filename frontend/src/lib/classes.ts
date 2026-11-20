/** Tissue codes in model output order. Must match backend/app/classes.py (checked by a test). */
export const CLASS_CODES = ['ADI', 'BACK', 'DEB', 'LYM', 'MUC', 'MUS', 'NORM', 'STR', 'TUM'] as const

export type ClassCode = (typeof CLASS_CODES)[number]

export const CLASS_NAMES: Record<ClassCode, string> = {
  ADI: 'Adipose tissue',
  BACK: 'Background',
  DEB: 'Debris',
  LYM: 'Lymphocytes',
  MUC: 'Mucus',
  MUS: 'Smooth muscle',
  NORM: 'Normal colon mucosa',
  STR: 'Cancer-associated stroma',
  TUM: 'Tumor epithelium',
}
