import { useState } from 'react'
import { useLab } from '../store/LabStore'

export function TracePanel() {
  const { state } = useLab()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  // Safe, desensitized single-trace mock JSON — no prompts / keys / sensitive params.
  const safeJson = JSON.stringify(
    state.trace.map((t) => ({
      event_index: t.eventIndex, stage: t.stage, tool_name: t.toolName, status: t.status,
      duration_ms: t.durationMs, structured: t.structured, evidence_count: t.evidenceCount,
      result_hash_short: t.resultHashShort,
    })), null, 2)

  async function copy() {
    try {
      await navigator.clipboard.writeText(safeJson)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard unavailable in some envs */ }
  }

  return (
    <div className="trace-wrap">
      <button className="btn subtle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide execution trace' : 'Show execution trace'}
      </button>
      {open && (
        <div className="trace-panel" data-testid="trace-panel">
          <div className="trace-note">仅展示脱敏执行元数据（不含完整 prompt / key / 敏感参数）。</div>
          <div className="trace-scroll">
            <table className="trace-table">
              <thead>
                <tr><th>#</th><th>阶段</th><th>工具</th><th>状态</th><th>耗时ms</th><th>结构化</th><th>证据</th><th>result hash</th></tr>
              </thead>
              <tbody>
                {state.trace.map((t) => (
                  <tr key={t.eventIndex}>
                    <td>{t.eventIndex}</td><td>{t.stage}</td><td>{t.toolName}</td><td>{t.status}</td>
                    <td>{t.durationMs}</td><td>{t.structured}</td><td>{t.evidenceCount}</td>
                    <td className="mono">{t.resultHashShort}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn subtle" onClick={copy}>{copied ? '已复制 ✓' : '复制单条 trace (mock JSON)'}</button>
        </div>
      )}
    </div>
  )
}
