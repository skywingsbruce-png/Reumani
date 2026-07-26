import { useState } from 'react'
import { useLab } from '../store/LabStore'

const QUICK = ['检索文献', '分析数据', '生成实验方案', '核查证据']

export function Composer() {
  const { dispatch } = useLab()
  const [text, setText] = useState('')
  const [auto, setAuto] = useState(true)

  function send() {
    const t = text.trim()
    if (!t) return
    dispatch({ type: 'send_message', text: t })
    setText('')
  }

  return (
    <div className="composer" aria-label="消息输入">
      <div className="quick" role="toolbar" aria-label="快捷操作">
        {QUICK.map((q) => (
          <button key={q} className="chip" onClick={() => setText((v) => (v ? `${v} ${q}` : q))}>{q}</button>
        ))}
      </div>
      <div className="composer-row">
        <textarea
          className="composer-input" rows={2} placeholder="向 Reumani 研究助手描述任务…（mock，不发起真实调用）"
          aria-label="消息输入框" value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send() } }}
        />
        <div className="composer-side">
          <button className="btn subtle" title="添加文件（mock）" aria-label="添加文件">＋ 文件</button>
          <span className="model-pill" title="占位：未连接真实模型">Research model</span>
          <label className="toggle">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            <span>自动执行</span>
          </label>
          <button className="btn primary" onClick={send} disabled={!text.trim()} aria-label="发送">发送 ⏎</button>
        </div>
      </div>
    </div>
  )
}
