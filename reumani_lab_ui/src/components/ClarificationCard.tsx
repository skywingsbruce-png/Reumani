import { useState } from 'react'
import { useLab } from '../store/LabStore'
import type { ClarificationRequest } from '../types'

function Card({ clar }: { clar: ClarificationRequest }) {
  const { dispatch } = useLab()
  const [selected, setSelected] = useState<string | null>(null)
  const [freeText, setFreeText] = useState('')
  const [error, setError] = useState<string | null>(null)

  if (clar.answered) {
    return (
      <div className="clar answered" aria-label="澄清已回答">
        <div className="clar-head"><span className="clar-ic" aria-hidden>✓</span><strong>澄清已回答</strong></div>
        <p className="clar-q">{clar.question}</p>
        <p className="clar-answer">你的回答：<strong>{clar.answerLabel}</strong></p>
      </div>
    )
  }

  const chosen = clar.options.find((o) => o.id === selected)
  const needsText = !!chosen?.allowFreeText

  function submit() {
    if (!selected) { setError('请先选择一个选项'); return }
    if (needsText && !freeText.trim()) { setError('请填写自定义内容'); return }
    const label = needsText ? `其他：${freeText.trim()}` : (chosen?.label ?? '')
    setError(null)
    dispatch({ type: 'answer_clarification', id: clar.id, answerLabel: label })
  }

  return (
    <div className="clar" role="group" aria-label="需要澄清">
      <div className="clar-head">
        <span className="clar-ic warn" aria-hidden>?</span>
        <strong>需要你的澄清</strong>
        <span className="badge b-warn"><span className="g" aria-hidden>⏸</span>步骤等待中</span>
      </div>
      <p className="clar-q">{clar.question}</p>
      <div className="clar-opts" role="radiogroup" aria-label={clar.question}>
        {clar.options.map((o) => (
          <label key={o.id} className={`opt ${selected === o.id ? 'sel' : ''}`}>
            <input type="radio" name={`clar-${clar.id}`} value={o.id}
              checked={selected === o.id}
              onChange={() => { setSelected(o.id); setError(null) }} />
            <span className="opt-body">
              <span className="opt-label">
                {o.label}
                {o.recommended && <span className="opt-rec" title="推荐（非强制）">推荐</span>}
              </span>
              {o.reason && <span className="opt-reason">{o.reason}</span>}
              {o.allowFreeText && selected === o.id && (
                <input className="clar-other" type="text" placeholder="输入其他值…"
                  aria-label="其他（自定义）" value={freeText}
                  onChange={(e) => setFreeText(e.target.value)} />
              )}
            </span>
          </label>
        ))}
      </div>
      {error && <div className="clar-error" role="alert">{error}</div>}
      <div className="clar-actions">
        <button className="btn primary" onClick={submit} disabled={!selected}>提交澄清</button>
      </div>
    </div>
  )
}

export function ClarificationCard() {
  const { state } = useLab()
  const clars = state.clarifications.filter((c) => c.taskId === state.currentTaskId)
  const open = clars.filter((c) => !c.answered)
  if (!clars.length) return null

  return (
    <section className="section clar-sec" aria-label="澄清请求" data-testid="clarifications">
      <h2 className="section-title">澄清请求{open.length ? ` · ${open.length} 待回答` : ''}</h2>
      {clars.map((c) => <Card key={c.id} clar={c} />)}
    </section>
  )
}
