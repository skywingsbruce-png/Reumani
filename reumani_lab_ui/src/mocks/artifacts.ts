import type { Artifact } from '../types'

export const mockArtifacts: Artifact[] = [
  {
    id: 'art-report', taskId: 'task-il6', name: 'Evidence report.md', kind: 'md',
    sizeBytes: 8_942, createdAt: '2026-07-24T09:38:00', stepId: 'step-5',
    verifierStatus: 'insufficient_for_causal', provenanceStatus: 'verified', hashShort: 'b3a1…9e02',
    previewKind: 'markdown',
    preview: [
      '# IL-6 与皮肤评分：证据分级 (mock)',
      '',
      '**resolution:** 关联证据充分；**causal_strength:** association',
      '',
      '- 现有为横断面相关证据，*不支持* 确定性因果。',
      '- 缺少纵向 / 干预证据。',
      '',
      '> Verifier: insufficient_for_causal（正确降级，非失败）。',
    ].join('\n'),
  },
  {
    id: 'art-records', taskId: 'task-il6', name: 'literature_records.json', kind: 'json',
    sizeBytes: 21_310, createdAt: '2026-07-24T09:33:00', stepId: 'step-1',
    verifierStatus: 'passed', provenanceStatus: 'verified', hashShort: '0d37…4ac3',
    previewKind: 'json',
    preview: JSON.stringify(
      { schema_version: 'toolresult-v1', tool_name: 'search_literature', ok: true,
        data: { retrieval_status: 'success', record_count: 3,
          records: [{ pmid: '4165****', content_level: 'abstract', content_hash: 'ca93…71be' }] } },
      null, 2),
  },
  {
    id: 'art-graph', taskId: 'task-il6', name: 'claim_evidence_graph.json', kind: 'json',
    sizeBytes: 4_120, createdAt: '2026-07-24T09:37:00', stepId: 'step-4',
    verifierStatus: 'pending', provenanceStatus: 'pending', hashShort: '7c1a…0b55',
    previewKind: 'json',
    preview: JSON.stringify(
      { claims: [{ id: 'c1', text: 'IL-6 与 mRSS 正相关', verdict: 'partially_supported' }] }, null, 2),
  },
  {
    id: 'art-causal', taskId: 'task-il6', name: 'causal_assessment.pdf', kind: 'pdf',
    sizeBytes: 154_002, createdAt: '2026-07-24T09:39:00', stepId: 'step-3',
    verifierStatus: 'not_run', provenanceStatus: 'pending', hashShort: '9f10…214e',
    previewKind: 'markdown', preview: '（PDF 预览占位）因果校准评估：association / temporal / intervention 分级。',
  },
  {
    id: 'art-trace', taskId: 'task-il6', name: 'execution_trace.jsonl', kind: 'jsonl',
    sizeBytes: 7_550, createdAt: '2026-07-24T09:39:00',
    verifierStatus: 'not_run', provenanceStatus: 'verified', hashShort: '8d3e…2d8c',
    previewKind: 'json',
    preview: JSON.stringify({ event: 'tool_returned', tool_name: 'search_literature', structured: 'structured', result_hash: '0d37…4ac3' }, null, 2),
  },
  {
    id: 'art-fig', taskId: 'task-il6', name: 'figure_il6_mrss.png', kind: 'png',
    sizeBytes: 88_400, createdAt: '2026-07-24T09:40:00', stepId: 'step-5',
    verifierStatus: 'not_run', provenanceStatus: 'missing', hashShort: 'e1dd…1f31',
    previewKind: 'image', preview: 'IL-6 vs mRSS scatter (mock)',
  },
  {
    id: 'art-table', taskId: 'task-il6', name: 'analysis_table.csv', kind: 'csv',
    sizeBytes: 3_002, createdAt: '2026-07-24T09:40:00', stepId: 'step-2',
    verifierStatus: 'passed', provenanceStatus: 'verified', hashShort: '2cf0…e8f7',
    previewKind: 'csv',
    preview: 'pmid,year,design,content_level\n4165****,2026,cross-sectional,abstract\n3330****,2020,cross-sectional,abstract',
  },
]
