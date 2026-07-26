import { useRef, useState } from 'react'
import { useLab, makeMockFile } from '../store/LabStore'
import { fileGlyph, formatBytes } from '../ui'
import { ConfirmDialog } from './ConfirmDialog'
import type { FileAsset } from '../types'

export function FileAssetPanel() {
  const { state, dispatch } = useLab()
  const [drag, setDrag] = useState(false)
  const [confirm, setConfirm] = useState<FileAsset | null>(null)
  const [preview, setPreview] = useState<FileAsset | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const q = state.fileSearch.trim().toLowerCase()
  const files = state.files.filter((f) => !q || f.name.toLowerCase().includes(q))

  function addFiles(list: FileList | null) {
    if (!list) return
    for (const f of Array.from(list)) {
      dispatch({ type: 'upload_file', file: makeMockFile(f.name, f.size) })
    }
  }

  return (
    <section className="files" aria-label="数据资产">
      <div className="section-head">
        <h2>数据资产</h2>
        <span className="mock-note" title="Mock：文件仅保存在浏览器本地，未上传服务器">Mock · 本地未上传</span>
      </div>
      <input className="search" type="search" placeholder="搜索文件…" aria-label="搜索文件"
        value={state.fileSearch} onChange={(e) => dispatch({ type: 'set_file_search', q: e.target.value })} />
      <div
        className={`dropzone ${drag ? 'drag' : ''}`}
        role="button" tabIndex={0} aria-label="上传文件（拖放或点击）"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files) }}
      >
        <span aria-hidden>⬆</span> 拖放或点击上传（mock）
      </div>
      <input ref={inputRef} type="file" multiple hidden aria-hidden data-testid="file-input"
        onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} />

      <ul className="file-list">
        {files.map((f) => (
          <li key={f.id} className="file-row">
            <button className="file-main" onClick={() => setPreview(f)} aria-label={`预览 ${f.name}`}>
              <span className="file-ic" aria-hidden>{fileGlyph[f.kind] ?? fileGlyph.other}</span>
              <span className="file-meta">
                <span className="file-name">{f.name}</span>
                <span className="file-sub">{formatBytes(f.sizeBytes)} · 解析 {f.parseStatus} · 溯源 {f.provenanceStatus}</span>
              </span>
            </button>
            <button className="icon-btn danger" aria-label={`删除 ${f.name}`} title="删除"
              onClick={() => setConfirm(f)}>✕</button>
          </li>
        ))}
        {!files.length && <li className="empty">无匹配文件</li>}
      </ul>

      {confirm && (
        <ConfirmDialog title="删除文件" body={`确定删除「${confirm.name}」？此操作仅影响本地 mock 列表。`}
          confirmLabel="删除"
          onCancel={() => setConfirm(null)}
          onConfirm={() => { dispatch({ type: 'delete_file', id: confirm.id }); setConfirm(null) }} />
      )}
      {preview && (
        <div className="dialog-overlay" role="dialog" aria-modal aria-label={`预览 ${preview.name}`} onClick={() => setPreview(null)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>{preview.name}</h3>
            <p className="preview-body">
              类型 {preview.kind} · {formatBytes(preview.sizeBytes)}<br />
              解析状态：{preview.parseStatus}<br />
              溯源状态：{preview.provenanceStatus}{preview.hashShort ? ` · ${preview.hashShort}` : ''}<br />
              <em>（Mock 预览占位：真实内容未加载，文件仅存于浏览器本地。）</em>
            </p>
            <div className="dialog-actions">
              <button className="btn subtle" onClick={() => setPreview(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
