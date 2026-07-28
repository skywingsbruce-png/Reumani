import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act, cleanup, within, fireEvent } from '@testing-library/react'
import { parseRuntimeEvent, type RuntimeEvent, EVENT_TYPES } from '../data/runtimeEvents'
import { applyRuntimeEvent, type RuntimeSlice } from '../data/eventMapping'
import { ApiDataSource } from '../data/ApiDataSource'
import { LabProvider, useLab, type LabContextValue } from '../store/LabStore'
import { AppShell } from '../components/AppShell'

let ctx: LabContextValue
function Capture() { ctx = useLab(); return null }

function mk(seq: number, type: string, extra: Partial<RuntimeEvent> = {}): RuntimeEvent {
  return {
    schema_version: 'reumani-event-v1', event_id: `r1-${seq}`, run_id: 'r1', sequence: seq,
    timestamp: '2026-07-27T09:30:00', event_type: type, step_id: null, status: null,
    summary: type, evidence_ids: [], artifact_ids: [], safe_payload: {}, content_hash: 'h',
    ...extra,
  }
}

beforeEach(() => { localStorage.clear(); cleanup() })

// ---------------------------- parse ----------------------------
describe('runtime event parsing', () => {
  it('accepts a valid event and exposes the shared enum', () => {
    expect(EVENT_TYPES).toContain('run_completed')
    const r = parseRuntimeEvent(mk(0, 'run_created'))
    expect(r.ok).toBe(true)
  })
  it('rejects unknown schema_version', () => {
    const r = parseRuntimeEvent({ ...mk(0, 'run_created'), schema_version: 'reumani-event-v2' })
    expect(r).toEqual({ ok: false, reason: 'bad_schema_version' })
  })
  it('flags unknown event_type (fail-closed)', () => {
    const r = parseRuntimeEvent({ ...mk(0, 'run_created'), event_type: 'mystery' })
    expect(r).toEqual({ ok: false, reason: 'unknown_event_type' })
  })
  it('rejects missing required field', () => {
    const bad: Record<string, unknown> = { ...mk(0, 'run_created') }
    delete bad.event_id
    expect(parseRuntimeEvent(bad).ok).toBe(false)
  })
})

// ---------------------------- mapping ----------------------------
function slice(): RuntimeSlice {
  return { taskId: 'r1', planSteps: [], timeline: [], trace: [], artifacts: [],
    runtime: { phase: 'running', elapsedMs: 0 }, runStatus: 'running' }
}

describe('event -> state mapping', () => {
  it('step_started creates a plan step; evidence_accumulated sets its count', () => {
    let s = applyRuntimeEvent(slice(), mk(0, 'step_started', { step_id: 1, safe_payload: { step_objective: '检索文献' } }))
    expect(s.planSteps).toHaveLength(1)
    s = applyRuntimeEvent(s, mk(1, 'evidence_accumulated', { step_id: 1, safe_payload: { evidence_count: 3 } }))
    expect(s.planSteps[0].evidenceCardCount).toBe(3)
  })
  it('terminal events set run status', () => {
    expect(applyRuntimeEvent(slice(), mk(9, 'run_completed')).runStatus).toBe('finished')
    expect(applyRuntimeEvent(slice(), mk(9, 'run_failed')).runStatus).toBe('failed')
    expect(applyRuntimeEvent(slice(), mk(9, 'run_stopped')).runStatus).toBe('stopped')
  })
  it('artifact_created adds an artifact', () => {
    const s = applyRuntimeEvent(slice(), mk(5, 'artifact_created', {
      artifact_ids: ['art-1'], safe_payload: { artifact_name: 'report.md', artifact_kind: 'md' } }))
    expect(s.artifacts[0].name).toBe('report.md')
  })
  it('tool_returned adds a trace row', () => {
    const s = applyRuntimeEvent(slice(), mk(4, 'tool_returned', { step_id: 1,
      safe_payload: { tool_name: 'search_literature', structured: true, retrieval_status: 'ok' } }))
    expect(s.trace[0].toolName).toBe('search_literature')
  })
})

// ---------------------------- ApiDataSource (injected fakes, no real network) ----------------------------
class FakeES {
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: ((e: unknown) => void) | null = null
  onopen: ((e: unknown) => void) | null = null
  closed = false
  close() { this.closed = true }
  emit(ev: RuntimeEvent) { this.onmessage?.({ data: JSON.stringify(ev) }) }
}

describe('ApiDataSource', () => {
  it('createRun + subscribe applies snapshot then stream; terminal closes', async () => {
    const es = new FakeES()
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith('/api/demo-runs')) return { json: async () => ({ run_id: 'r1' }) } as Response
      if (url.endsWith('/events')) return { json: async () => ({ events: [mk(0, 'run_created')] }) } as Response
      return {} as Response
    }) as unknown as typeof fetch
    const ds = new ApiDataSource('http://x', { fetchImpl, eventSourceFactory: () => es })
    const events: RuntimeEvent[] = []
    const conns: string[] = []
    const runId = await ds.createRun()
    expect(runId).toBe('r1')
    await ds.subscribe(runId, { onEvent: (e) => events.push(e), onConnection: (c) => conns.push(c) })
    es.onopen?.(null)
    es.emit(mk(1, 'plan_ready'))
    es.emit(mk(2, 'run_completed'))               // terminal → close
    expect(events.map((e) => e.event_type)).toEqual(['run_created', 'plan_ready', 'run_completed'])
    expect(es.closed).toBe(true)
    expect(conns).toContain('open')
    expect(conns).toContain('closed')
  })

  it('reports reconnecting on stream error and stop hits the endpoint', async () => {
    const es = new FakeES()
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith('/api/demo-runs')) return { json: async () => ({ run_id: 'r1' }) } as Response
      if (url.endsWith('/events')) return { json: async () => ({ events: [] }) } as Response
      return {} as Response
    }) as unknown as typeof fetch
    const ds = new ApiDataSource('http://x', { fetchImpl, eventSourceFactory: () => es })
    const conns: string[] = []
    await ds.subscribe('r1', { onEvent: () => {}, onConnection: (c) => conns.push(c) })
    es.onerror?.(new Error('drop'))
    expect(conns).toContain('reconnecting')
    await ds.stop('r1')
    expect((fetchImpl as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .some((c) => String(c[0]).endsWith('/api/runs/r1/stop'))).toBe(true)
  })
})

// ---------------------------- store integration (event-driven, no network) ----------------------------
function boot() {
  render(<LabProvider><Capture /><AppShell /></LabProvider>)
  act(() => { ctx.dispatch({ type: 'api_start', runId: 'r1' }) })
}

describe('API-mode store integration', () => {
  it('renders plan steps, evidence count, timeline and artifacts from events', () => {
    boot()
    act(() => {
      ctx.dispatch({ type: 'apply_event', ev: mk(0, 'run_created') })
      ctx.dispatch({ type: 'apply_event', ev: mk(1, 'step_started', { step_id: 1, safe_payload: { step_objective: '检索文献 (DEMO)' } }) })
      ctx.dispatch({ type: 'apply_event', ev: mk(2, 'evidence_accumulated', { step_id: 1, safe_payload: { evidence_count: 1 } }) })
      ctx.dispatch({ type: 'apply_event', ev: mk(3, 'step_satisfied', { step_id: 1, safe_payload: { remaining_gaps: ['缺少时序证据'] } }) })
      ctx.dispatch({ type: 'apply_event', ev: mk(4, 'artifact_created', { artifact_ids: ['art-1'], safe_payload: { artifact_name: 'evidence_report.md', artifact_kind: 'md' } }) })
    })
    expect(screen.getByText('检索文献 (DEMO)')).toBeInTheDocument()
    expect(within(screen.getByTestId('timeline')).getAllByText('step_started').length).toBeGreaterThan(0)
    expect(screen.getByText('evidence_report.md')).toBeInTheDocument()
    expect(screen.getByText(/EvidenceCard 1/)).toBeInTheDocument()
  })

  it('dedupes events by sequence (timeline idempotent on reconnect)', () => {
    boot()
    act(() => {
      ctx.dispatch({ type: 'apply_event', ev: mk(0, 'run_created') })
      ctx.dispatch({ type: 'apply_event', ev: mk(1, 'plan_ready') })
      ctx.dispatch({ type: 'apply_event', ev: mk(1, 'plan_ready') })   // replay same sequence
      ctx.dispatch({ type: 'apply_event', ev: mk(0, 'run_created') })  // replay older
    })
    // two unique events applied → two timeline rows (replays deduped by sequence)
    const rows = within(screen.getByTestId('timeline')).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
  })

  it('run_completed sets a finished run status', () => {
    boot()
    act(() => { ctx.dispatch({ type: 'apply_event', ev: mk(0, 'run_completed') }) })
    expect(screen.getByTestId('run-status').textContent).toContain('已完成')
  })

  it('run_failed shows an explicit failure status', () => {
    boot()
    act(() => { ctx.dispatch({ type: 'apply_event', ev: mk(0, 'run_failed') }) })
    expect(screen.getByTestId('run-status').textContent).toContain('失败')
  })

  it('canary meta renders a clearly-labelled banner with calls and cost', () => {
    boot()
    act(() => {
      ctx.dispatch({ type: 'set_canary_meta', meta: { canary_kind: 'real', model_calls: 3,
        usd_cost: 0.1234, causal_tier: 'association' } })
    })
    const banner = screen.getByTestId('canary-banner')
    expect(banner.textContent).toContain('Real model canary')
    expect(banner.textContent).toContain('Frozen real literature evidence')
    expect(screen.getByTestId('canary-calls').textContent).toBe('3')
    expect(screen.getByTestId('canary-cost').textContent).toBe('$0.1234')
  })

  it('fake canary banner is distinguished from real', () => {
    boot()
    act(() => { ctx.dispatch({ type: 'set_canary_meta', meta: { canary_kind: 'fake', model_calls: 3, usd_cost: 0 } }) })
    expect(screen.getByTestId('canary-banner').textContent).toContain('fake provider')
  })

  it('live run shows a Live run label (not the old demo label)', () => {
    render(<LabProvider><Capture /><AppShell /></LabProvider>)
    act(() => { ctx.dispatch({ type: 'api_start', runId: 'r1', replay: false }) })
    expect(screen.getByRole('heading', { level: 1, name: 'Live run' })).toBeInTheDocument()
    expect(screen.queryByText('Bounded open-task demo run')).not.toBeInTheDocument()
    expect(screen.queryByText(/offline demo run/)).not.toBeInTheDocument()
  })

  it('completed replay disables Stop with an explanation', () => {
    render(<LabProvider><Capture /><AppShell /></LabProvider>)
    act(() => { ctx.dispatch({ type: 'api_start', runId: 'r1', replay: true }) })
    expect(screen.getByRole('heading', { level: 1, name: 'Completed run replay' })).toBeInTheDocument()
    const stop = screen.getByTestId('stop-btn')
    expect(stop).toBeDisabled()
    expect(stop.getAttribute('title')).toContain('历史运行已完成')
  })
})

// ---------------------------- human-in-the-loop control (A.7.5) ----------------------------
function bootApi() {
  render(<LabProvider><Capture /><AppShell /></LabProvider>)
  act(() => { ctx.dispatch({ type: 'api_start', runId: 'r1', replay: false }) })
}

describe('HITL control UI', () => {
  it('renders a clarification card with options and gates Submit until a choice', () => {
    bootApi()
    act(() => { ctx.dispatch({ type: 'set_control', control: { control_state: 'awaiting_clarification',
      state_version: 1, pending: { type: 'clarification', request_id: 'clr-x', kind: 'single_or_other',
        allow_other: true, reason: '实验缺少组织来源', allowed_options: [
          { id: 'skin', label: '皮肤成纤维细胞' }, { id: 'lung', label: '肺成纤维细胞' }, { id: 'both', label: '两者' }] } } }) })
    const card = screen.getByTestId('hitl-clarification')
    expect(within(card).getByText('皮肤成纤维细胞')).toBeInTheDocument()
    expect(screen.getByTestId('clar-submit')).toBeDisabled()
    fireEvent.click(within(card).getByRole('radio', { name: /皮肤成纤维细胞/ }))
    expect(screen.getByTestId('clar-submit')).toBeEnabled()
    expect(screen.getByTestId('control-state').textContent).toContain('等待澄清')
  })

  it('renders an approval card with tool, risk, side-effect and Approve/Deny', () => {
    bootApi()
    act(() => { ctx.dispatch({ type: 'set_control', control: { control_state: 'awaiting_approval',
      state_version: 3, pending: { type: 'approval', request_id: 'apr-x', action_hash: 'abcdef0123456789',
        tool_name: 'simulate_wetlab_package', risk_level: 'high', action_summary: '模拟生成执行包',
        expected_side_effect: '不连接真实设备', is_simulation: true } } }) })
    const card = screen.getByTestId('hitl-approval')
    expect(within(card).getByText('simulate_wetlab_package')).toBeInTheDocument()
    expect(within(card).getByText(/风险：high/)).toBeInTheDocument()
    expect(within(card).getByText('仿真 / fake')).toBeInTheDocument()
    expect(screen.getByTestId('approve-btn')).toBeInTheDocument()
    expect(screen.getByTestId('deny-btn')).toBeInTheDocument()
  })

  it('shows a readable conflict message on control error', () => {
    bootApi()
    act(() => { ctx.dispatch({ type: 'set_control_error', error: '操作与最新状态冲突（stale）。请刷新后重试。' }) })
    expect(screen.getByTestId('control-error').textContent).toContain('冲突')
  })

  it('shows Pause when active and Resume when paused; disables during a write', () => {
    bootApi()
    act(() => { ctx.dispatch({ type: 'set_control', control: { control_state: 'awaiting_approval', state_version: 3, pending: null } }) })
    expect(screen.getByTestId('pause-btn')).toBeInTheDocument()
    act(() => { ctx.dispatch({ type: 'set_control', control: { control_state: 'paused', state_version: 4, pending: null } }) })
    expect(screen.getByTestId('resume-btn')).toBeInTheDocument()
    act(() => { ctx.dispatch({ type: 'set_control_busy', busy: true }) })
    expect(screen.getByTestId('resume-btn')).toBeDisabled()
  })

  it('drives control state from SSE events (clarification_requested → pending card)', () => {
    bootApi()
    act(() => {
      ctx.dispatch({ type: 'apply_event', ev: mk(0, 'clarification_requested', { step_id: 1,
        safe_payload: { control_state: 'awaiting_clarification', state_version: 1, request_id: 'clr-e',
          kind: 'single_or_other', allow_other: true, reason: '缺组织来源',
          allowed_options: [{ id: 'skin', label: '皮肤成纤维细胞' }] } }) })
    })
    expect(screen.getByTestId('hitl-clarification')).toBeInTheDocument()
    expect(screen.getByTestId('control-state').textContent).toContain('等待澄清')
  })
})

// ---------------------------- no scattered fetch ----------------------------
describe('data access discipline', () => {
  it('no component calls fetch/EventSource directly (only the data layer does)', () => {
    const comps = import.meta.glob('../components/*.tsx', { query: '?raw', eager: true, import: 'default' })
    for (const [path, src] of Object.entries(comps)) {
      expect(String(src), path).not.toMatch(/\bfetch\s*\(/)
      expect(String(src), path).not.toMatch(/new\s+EventSource/)
    }
  })
})
