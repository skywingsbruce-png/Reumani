import { createContext, useContext, useEffect, useMemo, useReducer, useRef, type Dispatch, type ReactNode } from 'react'
import type {
  Artifact, ClarificationRequest, FileAsset, PlanStep, Project, RuntimeState,
  TaskSession, TimelineEvent, TodoItem, TraceEvent,
} from '../types'
import { mockProjects, mockTasks } from '../mocks/projects'
import { mockFiles } from '../mocks/files'
import {
  mockClarifications, mockPlanSteps, mockTimeline, mockTodos, mockTrace,
} from '../mocks/tasks'
import { mockArtifacts } from '../mocks/artifacts'
import { ApiDataSource, type Connection } from '../data/ApiDataSource'
import { applyRuntimeEvent, type RunStatus, type RuntimeSlice } from '../data/eventMapping'
import type { RuntimeEvent } from '../data/runtimeEvents'

// ---- mock service boundary: a single place components read/write through. ----
// Later this can be replaced by a real API/SSE client without touching components.

interface LabState {
  projects: Project[]
  currentProjectId: string
  tasks: TaskSession[]
  currentTaskId: string
  files: FileAsset[]
  planSteps: PlanStep[]
  timeline: TimelineEvent[]
  trace: TraceEvent[]
  clarifications: ClarificationRequest[]
  todos: TodoItem[]
  artifacts: Artifact[]
  runtime: RuntimeState
  fileSearch: string
  taskSearch: string
  // API mode (event-driven). In mock mode these keep their defaults.
  mode: 'mock' | 'api'
  runStatus: RunStatus
  connection: Connection
  apiRunId: string | null
  appliedSeq: number          // highest applied event sequence (idempotency)
  canaryMeta: Record<string, unknown> | null   // desensitized canary meta (calls/cost/tier)
  replay: boolean             // read-only replay of a completed run (Stop is disabled)
  // Human-in-the-loop control (A.7.5)
  controlState: string | null           // running / awaiting_clarification / awaiting_approval / paused / ...
  controlVersion: number                // expected_state_version for the next write
  pending: Record<string, unknown> | null   // pending clarification/approval card
  runType: string | null                // 'demo' | 'research' (A.7.5.3)
  research: Record<string, unknown> | null  // stages, current stage, verdicts, executor id
  controlError: string | null           // last 409/stale-state message (readable)
  controlBusy: boolean                  // a control write is in flight (buttons disabled)
}

const CONTROL_DEFAULTS = {
  controlState: null as string | null, controlVersion: 0,
  pending: null as Record<string, unknown> | null, controlError: null as string | null,
  controlBusy: false,
  runType: null as string | null,                       // 'demo' | 'research'
  research: null as Record<string, unknown> | null,     // stages / verdicts / executor (A.7.5.3)
}

type Action =
  | { type: 'select_project'; id: string }
  | { type: 'select_task'; id: string }
  | { type: 'upload_file'; file: FileAsset }
  | { type: 'delete_file'; id: string }
  | { type: 'set_file_search'; q: string }
  | { type: 'set_task_search'; q: string }
  | { type: 'answer_clarification'; id: string; answerLabel: string }
  | { type: 'send_message'; text: string }
  | { type: 'runtime_tick'; ms: number }
  | { type: 'runtime_stop' }
  | { type: 'runtime_resume' }
  | { type: 'api_start'; runId: string; replay?: boolean }
  | { type: 'apply_event'; ev: RuntimeEvent }
  | { type: 'set_connection'; connection: Connection }
  | { type: 'set_canary_meta'; meta: Record<string, unknown> }
  | { type: 'set_control'; control: Record<string, unknown> }
  | { type: 'set_control_error'; error: string | null }
  | { type: 'set_control_busy'; busy: boolean }

const LS_KEY = 'reumani-lab-ui-v1'

function seed(): LabState {
  return {
    projects: mockProjects,
    currentProjectId: mockProjects[0].id,
    tasks: mockTasks,
    currentTaskId: 'task-il6',
    files: mockFiles,
    planSteps: mockPlanSteps,
    timeline: mockTimeline,
    trace: mockTrace,
    clarifications: mockClarifications,
    todos: mockTodos,
    artifacts: mockArtifacts,
    runtime: { phase: 'running', elapsedMs: 128_000 },
    fileSearch: '',
    taskSearch: '',
    mode: 'mock',
    runStatus: 'running',
    connection: 'closed',
    apiRunId: null,
    appliedSeq: -1,
    canaryMeta: null,
    replay: false,
    ...CONTROL_DEFAULTS,
  }
}

// Fresh empty state for API mode — one runtime project/task, everything else event-driven.
// `replay` = read-only replay of a completed run (vs a live run being streamed now).
function apiSeed(runId: string, replay: boolean): LabState {
  return {
    projects: [{ id: 'live', name: 'Reumani runtime',
                 subtitle: replay ? 'Completed run replay · read-only（历史运行）' : 'Live run · API mode' }],
    currentProjectId: 'live',
    tasks: [{ id: runId, projectId: 'live', title: replay ? 'Completed run replay' : 'Live run',
              status: 'running', group: 'running', updatedAt: new Date().toISOString() }],
    currentTaskId: runId,
    files: [], planSteps: [], timeline: [], trace: [], clarifications: [], todos: [], artifacts: [],
    runtime: { phase: 'running', elapsedMs: 0 },
    fileSearch: '', taskSearch: '',
    mode: 'api', runStatus: 'running', connection: 'connecting', apiRunId: runId, appliedSeq: -1,
    canaryMeta: null, replay, ...CONTROL_DEFAULTS,
  }
}

function nowClock(): string {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

let uid = 0
const nextId = (p: string) => `${p}-${Date.now().toString(36)}-${uid++}`

function reducer(state: LabState, action: Action): LabState {
  switch (action.type) {
    case 'select_project':
      return { ...state, currentProjectId: action.id }
    case 'select_task':
      return { ...state, currentTaskId: action.id }
    case 'set_file_search':
      return { ...state, fileSearch: action.q }
    case 'set_task_search':
      return { ...state, taskSearch: action.q }
    case 'upload_file':
      return { ...state, files: [action.file, ...state.files] }
    case 'delete_file':
      return { ...state, files: state.files.filter((f) => f.id !== action.id) }
    case 'answer_clarification': {
      const clar = state.clarifications.find((c) => c.id === action.id)
      const clarifications = state.clarifications.map((c) =>
        c.id === action.id ? { ...c, answered: true, answerLabel: action.answerLabel } : c)
      const todos = state.todos.filter((t) => t.clarificationId !== action.id)
      const timeline: TimelineEvent[] = [
        ...state.timeline,
        {
          id: nextId('ev'), taskId: state.currentTaskId, type: 'clarification_answered',
          at: nowClock(), title: '用户回答澄清', detail: `${action.answerLabel}`,
        },
      ]
      // if no more open clarifications on the blocked step, unblock it → running
      const stepId = clar?.stepId
      const stillOpen = clarifications.some((c) => c.stepId === stepId && !c.answered)
      const planSteps = state.planSteps.map((s) =>
        s.id === stepId && !stillOpen && (s.status === 'blocked' || s.status === 'pending')
          ? { ...s, status: 'running' as const, attempts: s.attempts + 1 }
          : s)
      if (!stillOpen) {
        timeline.push({
          id: nextId('ev'), taskId: state.currentTaskId, type: 'resumed', at: nowClock(),
          title: '继续执行', detail: '澄清完成，步骤恢复 running',
        })
      }
      return { ...state, clarifications, todos, timeline, planSteps }
    }
    case 'send_message': {
      const timeline: TimelineEvent[] = [
        ...state.timeline,
        { id: nextId('ev'), taskId: state.currentTaskId, type: 'user_message', at: nowClock(), title: '用户消息', detail: action.text },
        { id: nextId('ev'), taskId: state.currentTaskId, type: 'plan_created', at: nowClock(), title: 'Agent 状态更新 (mock)', detail: '已排入队列，未发起任何真实调用' },
      ]
      return { ...state, timeline, runtime: { ...state.runtime, phase: 'running' } }
    }
    case 'runtime_tick':
      if (state.runtime.phase !== 'running') return state
      return { ...state, runtime: { ...state.runtime, elapsedMs: state.runtime.elapsedMs + action.ms } }
    case 'runtime_stop': {
      const timeline: TimelineEvent[] = [
        ...state.timeline,
        { id: nextId('ev'), taskId: state.currentTaskId, type: 'stopped', at: nowClock(), title: '已停止', status: 'stopped', detail: '用户点击 Stop（mock）' },
      ]
      const planSteps = state.planSteps.map((s) =>
        s.status === 'running' ? { ...s, status: 'blocked' as const } : s)
      return { ...state, runtime: { ...state.runtime, phase: 'stopped' }, timeline, planSteps }
    }
    case 'runtime_resume': {
      const timeline: TimelineEvent[] = [
        ...state.timeline,
        { id: nextId('ev'), taskId: state.currentTaskId, type: 'resumed', at: nowClock(), title: '已恢复', detail: '用户点击 Resume（mock）' },
      ]
      return { ...state, runtime: { ...state.runtime, phase: 'running' }, timeline }
    }
    case 'api_start':
      return apiSeed(action.runId, action.replay ?? false)
    case 'set_connection':
      return { ...state, connection: action.connection }
    case 'set_canary_meta':
      return { ...state, canaryMeta: action.meta }
    case 'set_control': {
      const c = action.control
      return { ...state,
        controlState: (c.control_state as string) ?? state.controlState,
        controlVersion: (c.state_version as number) ?? state.controlVersion,
        pending: (c.pending as Record<string, unknown> | null) ?? null,
        runType: (c.run_type as string) ?? state.runType,
        research: (c.research as Record<string, unknown> | null) ?? state.research,
        controlError: null, controlBusy: false }
    }
    case 'set_control_error':
      return { ...state, controlError: action.error, controlBusy: false }
    case 'set_control_busy':
      return { ...state, controlBusy: action.busy }
    case 'apply_event': {
      const ev = action.ev
      if (ev.run_id !== state.apiRunId) return state
      if (ev.sequence <= state.appliedSeq) return state          // idempotent: dedupe by sequence
      const slice: RuntimeSlice = {
        taskId: state.currentTaskId, planSteps: state.planSteps, timeline: state.timeline,
        trace: state.trace, artifacts: state.artifacts, runtime: state.runtime,
        runStatus: state.runStatus,
      }
      const next = applyRuntimeEvent(slice, ev)
      const terminal = ev.event_type === 'run_completed' || ev.event_type === 'run_failed'
        || ev.event_type === 'run_stopped'
      const tasks = terminal
        ? state.tasks.map((t) => t.id === state.currentTaskId
            ? { ...t, status: next.runStatus === 'finished' ? 'completed' as const
                : 'failed' as const,
                group: next.runStatus === 'finished' ? 'completed' as const : 'failed' as const }
            : t)
        : state.tasks
      // Human-in-the-loop control is event-driven too (refresh replay + multi-browser via SSE).
      const sp = ev.safe_payload
      const controlState = (sp.control_state as string) ?? state.controlState
      const controlVersion = (sp.state_version as number) ?? state.controlVersion
      let pending = state.pending
      // Keep any richer fields the snapshot already supplied for the SAME request (e.g. research
      // prompt/recommended/policy), so an SSE replay never downgrades an already-rendered card.
      const keep = (rid: unknown) =>
        state.pending && state.pending.request_id === rid ? state.pending : {}
      if (ev.event_type === 'clarification_requested') {
        pending = { ...keep(sp.request_id),
          type: 'clarification', request_id: sp.request_id, kind: sp.kind,
          allow_other: sp.allow_other, reason: sp.reason, allowed_options: sp.allowed_options,
          ...(sp.note ? { prompt: sp.note } : {}) }
      } else if (ev.event_type === 'approval_requested') {
        pending = { ...keep(sp.request_id),
          type: 'approval', request_id: sp.request_id, action_hash: sp.action_hash,
          tool_name: sp.tool_name, risk_level: sp.risk_level, action_summary: sp.action_summary,
          expected_side_effect: sp.expected_side_effect, is_simulation: sp.is_simulation,
          reason: sp.reason,
          ...(sp.run_type ? { run_type: sp.run_type } : {}),
          ...(sp.executor_id ? { executor_id: sp.executor_id } : {}),
          ...(sp.evidence_count !== undefined ? { evidence_count: sp.evidence_count } : {}),
          ...(sp.policy_hash ? { policy_hash: sp.policy_hash } : {}) }
      } else if (ev.event_type === 'clarification_answered' || ev.event_type === 'approval_granted'
                 || ev.event_type === 'approval_denied') {
        pending = null
      }
      // Research runs (A.7.5.3): stage timeline + verdicts derived from SSE, same as control state.
      const runType = (sp.run_type as string) ?? state.runType
      let research = state.research
      if (sp.run_type === 'research' || sp.stage || sp.executor_id) {
        const prev = (research ?? {}) as Record<string, unknown>
        const done = Array.isArray(prev.stages_done) ? [...(prev.stages_done as string[])] : []
        if (ev.event_type === 'research_stage_completed' && sp.stage
            && !done.includes(sp.stage as string)) done.push(sp.stage as string)
        research = { ...prev,
          executor_id: sp.executor_id ?? prev.executor_id,
          stage_count: sp.stage_count ?? prev.stage_count,
          stages_done: done,
          current_stage: ev.event_type === 'research_stage_started' ? sp.stage
            : ev.event_type === 'research_stage_completed' ? null : prev.current_stage,
          verifier_verdict: sp.verifier_verdict ?? prev.verifier_verdict,
          shadow_verdict: sp.shadow_verdict ?? prev.shadow_verdict,
          causal_tier: sp.causal_tier ?? prev.causal_tier,
          claim_count: sp.claim_count ?? prev.claim_count,
          evidence_count: sp.evidence_count ?? prev.evidence_count,
          fixture: sp.fixture ?? prev.fixture }
      }
      return {
        ...state, planSteps: next.planSteps, timeline: next.timeline, trace: next.trace,
        artifacts: next.artifacts, runtime: next.runtime, runStatus: next.runStatus,
        tasks, appliedSeq: ev.sequence, controlState, controlVersion, pending, runType, research,
      }
    }
    default:
      return state
  }
}

export interface LabContextValue {
  state: LabState
  dispatch: Dispatch<Action>
  // convenience derived selectors
  currentProject: () => Project | undefined
  currentTask: () => TaskSession | undefined
  requestStop: () => void       // api mode → real stop endpoint; mock mode → local stop
  // human-in-the-loop control (api mode) — each calls the real backend endpoint
  answerClarification: (requestId: string, selectedIds: string[], otherText?: string) => void
  decideApproval: (requestId: string, approve: boolean, actionHash: string) => void
  pauseRun: () => void
  resumeRun: () => void
}

let _idem = 0
const nextIdem = (p: string) => `${p}-${Date.now().toString(36)}-${_idem++}`

function apiConfig(): { base: string; real: boolean; canaryFake: boolean; hitl: boolean;
                        research?: string; fixedRunId?: string } | null {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {}
  if (env.VITE_REUMANI_DATA_SOURCE !== 'api') return null
  const canary = env.VITE_REUMANI_CANARY               // 'fake' | 'real' | undefined
  const research = env.VITE_REUMANI_RESEARCH_EXECUTOR  // executor id → parameterized research run
  return {
    base: env.VITE_REUMANI_API_BASE || 'http://127.0.0.1:8799',
    real: env.VITE_REUMANI_DEMO_REAL === '1',
    canaryFake: canary === 'fake',
    hitl: env.VITE_REUMANI_HITL === '1' || !!research,
    research: research || undefined,
    fixedRunId: canary === 'real' ? env.VITE_REUMANI_RUN_ID : undefined,   // serve a completed real canary
  }
}

const LabContext = createContext<LabContextValue | null>(null)

function loadPersisted(): Partial<LabState> | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? (JSON.parse(raw) as Partial<LabState>) : null
  } catch {
    return null
  }
}

export function LabProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, () => {
    const base = seed()
    const persisted = loadPersisted()
    // Only persist the user-driven parts so the demo still looks complete.
    if (persisted) {
      return {
        ...base,
        clarifications: persisted.clarifications ?? base.clarifications,
        todos: persisted.todos ?? base.todos,
        files: persisted.files ?? base.files,
        planSteps: persisted.planSteps ?? base.planSteps,
      }
    }
    return base
  })

  // persist user-driven state (mock mode only — API state is event-driven, not authoritative here)
  useEffect(() => {
    if (state.mode === 'api') return
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        clarifications: state.clarifications, todos: state.todos,
        files: state.files, planSteps: state.planSteps,
      }))
    } catch { /* ignore */ }
  }, [state.mode, state.clarifications, state.todos, state.files, state.planSteps])

  // runtime timer (front-end only)
  const rafRef = useRef<number | null>(null)
  useEffect(() => {
    const t = window.setInterval(() => dispatch({ type: 'runtime_tick', ms: 1000 }), 1000)
    return () => window.clearInterval(t)
  }, [])
  useEffect(() => () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }, [])

  // API mode: create a demo run, register it, then stream real backend events.
  const dsRef = useRef<ApiDataSource | null>(null)
  const runIdRef = useRef<string | null>(null)
  useEffect(() => {
    const cfg = apiConfig()
    if (!cfg) return
    // HITL runs must survive refresh: persist the run id and re-subscribe to the SAME run
    // (interactive, not a replay). Canary "real" mode uses a fixed run id as a read-only replay.
    // ?run=<id> attaches this tab to an existing HITL run (interactive) so two browsers can
    // drive the same run; otherwise a HITL tab persists its own run id across refresh.
    const urlRun = cfg.hitl
      ? new URLSearchParams(window.location.search).get('run') || undefined : undefined
    const HITL_KEY = `reumani-hitl-run-${cfg.base}`
    const savedHitl = cfg.hitl ? (urlRun || sessionStorage.getItem(HITL_KEY) || undefined) : undefined
    const fixedRunId = cfg.fixedRunId || savedHitl
    const isReplay = !!cfg.fixedRunId          // only canary-real is a read-only replay
    const ds = new ApiDataSource(cfg.base, { real: cfg.real, canaryFake: cfg.canaryFake,
                                             hitl: cfg.hitl && !savedHitl, fixedRunId,
                                             researchExecutor: savedHitl ? undefined : cfg.research })
    dsRef.current = ds
    let cancelled = false
    void (async () => {
      try {
        const runId = await ds.createRun()
        if (cancelled) return
        if (cfg.hitl) { try { sessionStorage.setItem(HITL_KEY, runId) } catch { /* ignore */ } }
        runIdRef.current = runId
        dispatch({ type: 'api_start', runId, replay: isReplay })
        await ds.subscribe(runId, {
          onEvent: (ev) => dispatch({ type: 'apply_event', ev }),
          onConnection: (c) => dispatch({ type: 'set_connection', connection: c }),
          onCanary: (meta) => dispatch({ type: 'set_canary_meta', meta }),
          onControl: (control) => dispatch({ type: 'set_control', control }),
        })
      } catch {
        dispatch({ type: 'set_connection', connection: 'reconnecting' })
      }
    })()
    return () => { cancelled = true; ds.dispose() }
  }, [])

  // one control write at a time; applies {control} on success or a readable 409 message
  const runControl = (path: string, body: Record<string, unknown>) => {
    const ds = dsRef.current, runId = runIdRef.current
    if (!ds || !runId) return
    dispatch({ type: 'set_control_busy', busy: true })
    void ds.control(path, body).then((r) => {
      if (r.status === 200 && r.control) dispatch({ type: 'set_control', control: r.control })
      else if (r.status === 409) dispatch({ type: 'set_control_error',
        error: `操作与最新状态冲突（${r.error ?? 'stale state'}）。请刷新后重试。` })
      else dispatch({ type: 'set_control_error', error: r.error ?? `请求失败（${r.status}）` })
    }).catch(() => dispatch({ type: 'set_control_error', error: '网络错误，请重试' }))
  }

  const value = useMemo<LabContextValue>(() => ({
    state, dispatch,
    currentProject: () => state.projects.find((p) => p.id === state.currentProjectId),
    currentTask: () => state.tasks.find((t) => t.id === state.currentTaskId),
    requestStop: () => {
      if (state.mode === 'api' && runIdRef.current && dsRef.current) {
        void dsRef.current.stop(runIdRef.current)   // run_stopped will arrive via SSE
      } else {
        dispatch({ type: 'runtime_stop' })
      }
    },
    answerClarification: (requestId, selectedIds, otherText) => {
      const body: Record<string, unknown> = { expected_state_version: state.controlVersion,
        idempotency_key: nextIdem('clr'), selected_option_ids: selectedIds }
      if (otherText) body.other_text = otherText
      runControl(`/api/runs/${runIdRef.current}/clarifications/${requestId}/answer`, body)
    },
    decideApproval: (requestId, approve, actionHash) => {
      const verb = approve ? 'approve' : 'deny'
      runControl(`/api/runs/${runIdRef.current}/approvals/${requestId}/${verb}`, {
        expected_state_version: state.controlVersion, idempotency_key: nextIdem(verb),
        action_hash: actionHash })
    },
    pauseRun: () => runControl(`/api/runs/${runIdRef.current}/pause`,
      { idempotency_key: nextIdem('pause'), expected_state_version: state.controlVersion }),
    resumeRun: () => runControl(`/api/runs/${runIdRef.current}/resume`,
      { idempotency_key: nextIdem('resume'), expected_state_version: state.controlVersion }),
  }), [state])

  return <LabContext.Provider value={value}>{children}</LabContext.Provider>
}

export function useLab(): LabContextValue {
  const ctx = useContext(LabContext)
  if (!ctx) throw new Error('useLab must be used within LabProvider')
  return ctx
}

export function makeMockFile(name: string, sizeBytes: number): FileAsset {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  const kind: FileAsset['kind'] =
    ext === 'pdf' ? 'pdf' : ext === 'csv' ? 'csv' : ext === 'json' ? 'json'
      : ['png', 'jpg', 'jpeg', 'gif', 'svg'].includes(ext) ? 'image'
        : ext === 'fasta' ? 'fasta' : 'other'
  return {
    id: nextId('file'), name, kind, sizeBytes,
    uploadedAt: new Date().toISOString(), parseStatus: 'parsing',
    provenanceStatus: 'pending', referencedByTaskId: null, mock: true,
  }
}
