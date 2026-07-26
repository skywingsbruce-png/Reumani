import { useLab } from '../store/LabStore'

function fmt(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const PHASE_LABEL: Record<string, string> = {
  idle: '空闲', running: '运行中', stopping: '停止中', stopped: '已停止',
}

export function RuntimeControl() {
  const { state, dispatch } = useLab()
  const { phase, elapsedMs } = state.runtime
  return (
    <div className="runtime" aria-label="运行控制">
      <span className="runtime-clock" aria-label={`已运行 ${fmt(elapsedMs)}`}>
        <span aria-hidden>⏱</span> <span className="mono" data-testid="runtime-clock">{fmt(elapsedMs)}</span>
      </span>
      <span className={`badge ${phase === 'running' ? 'b-run' : phase === 'stopped' ? 'b-fail' : 'b-idle'}`}>
        <span className="g" aria-hidden>{phase === 'running' ? '▶' : phase === 'stopped' ? '■' : '○'}</span>
        {PHASE_LABEL[phase]}
      </span>
      {phase === 'running'
        ? <button className="btn danger" onClick={() => dispatch({ type: 'runtime_stop' })}>■ Stop</button>
        : <button className="btn subtle" onClick={() => dispatch({ type: 'runtime_resume' })}>▶ Resume</button>}
    </div>
  )
}
