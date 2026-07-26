import type { FileAsset } from '../types'

export const mockFiles: FileAsset[] = [
  {
    id: 'file-pigr-pdf',
    name: 'pIgR_internalization_protocol.pdf',
    kind: 'pdf',
    sizeBytes: 482_112,
    uploadedAt: '2026-07-24T08:50:00',
    parseStatus: 'parsed',
    provenanceStatus: 'verified',
    hashShort: 'a1c9…71be',
    referencedByTaskId: 'task-pigr',
    mock: true,
  },
  {
    id: 'file-il6-csv',
    name: 'il6_mrss_cohort.csv',
    kind: 'csv',
    sizeBytes: 96_640,
    uploadedAt: '2026-07-24T09:05:00',
    parseStatus: 'parsed',
    provenanceStatus: 'pending',
    hashShort: '5d26…fd2b',
    referencedByTaskId: 'task-il6',
    mock: true,
  },
  {
    id: 'file-guide-fasta',
    name: 'stat3_locus.fasta',
    kind: 'fasta',
    sizeBytes: 12_300,
    uploadedAt: '2026-07-23T17:40:00',
    parseStatus: 'unparsed',
    provenanceStatus: 'missing',
    referencedByTaskId: null,
    mock: true,
  },
]
