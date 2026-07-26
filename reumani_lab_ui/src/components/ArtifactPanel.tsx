import { useState } from 'react'
import { useLab } from '../store/LabStore'
import { artGlyph, formatBytes, VerifierBadge } from '../ui'
import { ArtifactPreview } from './ArtifactPreview'
import type { Artifact } from '../types'

const MIME: Record<Artifact['kind'], string> = {
  md: 'text/markdown', json: 'application/json', pdf: 'application/pdf',
  png: 'text/plain', csv: 'text/csv', jsonl: 'application/x-ndjson',
}

export function ArtifactPanel() {
  const { state } = useLab()
  const [preview, setPreview] = useState<Artifact | null>(null)
  const artifacts = state.artifacts.filter((a) => a.taskId === state.currentTaskId)

  function download(a: Artifact) {
    // mock download only — a Blob from the desensitized preview, never a network fetch.
    const blob = new Blob([a.preview], { type: MIME[a.kind] })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = a.name
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <div className="right-panel results" aria-label="结果与产出物">
      <div className="section-head">
        <h2 className="section-title">结果 / 产出物 <span className="count">{artifacts.length}</span></h2>
      </div>
      <ul className="art-list">
        {artifacts.map((a) => (
          <li key={a.id} className="art">
            <div className="art-top">
              <span className="art-ic" aria-hidden>{artGlyph[a.kind] ?? '▢'}</span>
              <button className="art-name" onClick={() => setPreview(a)} aria-label={`预览 ${a.name}`}>{a.name}</button>
            </div>
            <div className="art-meta">
              {a.kind} · {formatBytes(a.sizeBytes)} · {a.stepId ?? '—'} · <span className="mono">{a.hashShort ?? '—'}</span>
            </div>
            <div className="art-badges">
              <VerifierBadge status={a.verifierStatus} />
              <span className="prov" title="溯源状态">溯源：{a.provenanceStatus}</span>
            </div>
            <div className="art-actions">
              <button className="btn subtle sm" onClick={() => setPreview(a)}>预览</button>
              <button className="btn subtle sm" onClick={() => download(a)}>下载（mock）</button>
            </div>
          </li>
        ))}
        {!artifacts.length && <li className="empty">暂无产出物</li>}
      </ul>
      {preview && <ArtifactPreview artifact={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}
