import type { Artifact } from '../types'

export function ArtifactPreview({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  return (
    <div className="preview-overlay" role="dialog" aria-modal aria-label={`预览 ${artifact.name}`}
      onClick={onClose} data-testid="artifact-preview">
      <div className="preview" onClick={(e) => e.stopPropagation()}>
        <div className="preview-head">
          <h3>{artifact.name}</h3>
          <button className="icon-btn" aria-label="关闭预览" onClick={onClose}>✕</button>
        </div>
        <div className="preview-meta mono">
          {artifact.kind} · {artifact.hashShort ?? '—'} · 溯源 {artifact.provenanceStatus}
        </div>
        <div className="preview-content">
          {artifact.previewKind === 'markdown' && <pre className="pre-md">{artifact.preview}</pre>}
          {artifact.previewKind === 'json' && <pre className="pre-code">{artifact.preview}</pre>}
          {artifact.previewKind === 'csv' && (
            <table className="csv-table">
              <tbody>
                {artifact.preview.split('\n').map((row, i) => (
                  <tr key={i} className={i === 0 ? 'csv-head' : ''}>
                    {row.split(',').map((cell, j) => <td key={j}>{cell}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {artifact.previewKind === 'image' && (
            <div className="img-ph" role="img" aria-label={artifact.preview}>
              <span aria-hidden>▣</span>
              <span>{artifact.preview}</span>
            </div>
          )}
        </div>
        <div className="preview-foot">
          <em>Mock 预览：内容已脱敏，未包含密钥或患者数据。</em>
        </div>
      </div>
    </div>
  )
}
