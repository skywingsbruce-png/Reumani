import { useLab } from '../store/LabStore'

function fmt(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const PHASE_LABEL: Record<string, string> = {
  idle: '空闲', running: '运行中', stopping: '停止中', stopped: '已停止',
}
const RUN_STATUS: Record<string, { label: string; c: string; g: string }> = {
  running: { label: '运行中', c: 'b-run', g: '▶' },
  finished: { label: '已完成', c: 'b-ok', g: '✓' },
  failed: { label: '失败', c: 'b-fail', g: '✕' },
  stopped: { label: '已停止', c: 'b-fail', g: '■' },
}
const CONN: Record<string, { label: string; c: string }> = {
  connecting: { label: '连接中', c: 'b-idle' },
  open: { label: '已连接', c: 'b-run' },
  reconnecting: { label: '重连中…', c: 'b-warn' },
  closed: { label: '已断开', c: 'b-idle' },
}
const CTRL: Record<string, { label: string; c: string }> = {
  running: { label: '运行中', c: 'b-run' },
  awaiting_clarification: { label: '等待澄清', c: 'b-warn' },
  awaiting_approval: { label: '等待审批', c: 'b-warn' },
  pausing: { label: '暂停中…', c: 'b-warn' },
  paused: { label: '已暂停', c: 'b-idle' },
  completed: { label: '已完成', c: 'b-ok' },
  failed: { label: '失败', c: 'b-fail' },
  stopped: { label: '已停止', c: 'b-fail' },
}

export function RuntimeControl() {
  const { state, dispatch, requestStop, pauseRun, resumeRun } = useLab()
  const { phase, elapsedMs } = state.runtime
  const api = state.mode === 'api'
  const rs = RUN_STATUS[state.runStatus] ?? RUN_STATUS.running
  const conn = CONN[state.connection] ?? CONN.closed
  const running = phase === 'running'

  return (
    <div className="runtime" aria-label="运行控制">
      <span className="runtime-clock" aria-label={`已运行 ${fmt(elapsedMs)}`}>
        <span aria-hidden>⏱</span> <span className="mono" data-testid="runtime-clock">{fmt(elapsedMs)}</span>
      </span>
      {api ? (
        <>
          <span className={`badge ${rs.c}`} data-testid="run-status">
            <span className="g" aria-hidden>{rs.g}</span>{rs.label}
          </span>
          {state.controlState && !state.replay && (
            <span className={`badge ${CTRL[state.controlState]?.c ?? 'b-idle'}`} data-testid="control-state">
              {CTRL[state.controlState]?.label ?? state.controlState}
            </span>
          )}
          {state.connection === 'reconnecting' && (
            <span className={`badge ${conn.c}`} data-testid="connection">
              <span className="g" aria-hidden>↻</span>{conn.label}
            </span>
          )}
          {!state.replay && state.controlState && !['completed', 'failed', 'stopped'].includes(state.controlState) && (
            state.controlState === 'paused'
              ? <button className="btn subtle" data-testid="resume-btn" disabled={state.controlBusy}
                  onClick={resumeRun}>▶ Resume</button>
              : <button className="btn subtle" data-testid="pause-btn" disabled={state.controlBusy}
                  onClick={pauseRun}>⏸ Pause</button>
          )}
          {state.replay ? (
            <button className="btn subtle" data-testid="stop-btn" disabled
              title="历史运行已完成（只读回放），不能停止">■ Stop（已完成）</button>
          ) : (
            <button className="btn danger" data-testid="stop-btn"
              disabled={!!state.controlState && ['completed', 'failed', 'stopped'].includes(state.controlState)}
              onClick={requestStop}>■ Stop</button>
          )}
        </>
      ) : (
        <>
          <span className={`badge ${running ? 'b-run' : phase === 'stopped' ? 'b-fail' : 'b-idle'}`}>
            <span className="g" aria-hidden>{running ? '▶' : phase === 'stopped' ? '■' : '○'}</span>
            {PHASE_LABEL[phase]}
          </span>
          {running
            ? <button className="btn danger" onClick={requestStop}>■ Stop</button>
            : <button className="btn subtle" onClick={() => dispatch({ type: 'runtime_resume' })}>▶ Resume</button>}
        </>
      )}
    </div>
  )
}
