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
    canaryMeta: null, replay,
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
      return {
        ...state, planSteps: next.planSteps, timeline: next.timeline, trace: next.trace,
        artifacts: next.artifacts, runtime: next.runtime, runStatus: next.runStatus,
        tasks, appliedSeq: ev.sequence,
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
}

function apiConfig(): { base: string; real: boolean; canaryFake: boolean; fixedRunId?: string } | null {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {}
  if (env.VITE_REUMANI_DATA_SOURCE !== 'api') return null
  const canary = env.VITE_REUMANI_CANARY               // 'fake' | 'real' | undefined
  return {
    base: env.VITE_REUMANI_API_BASE || 'http://127.0.0.1:8799',
    real: env.VITE_REUMANI_DEMO_REAL === '1',
    canaryFake: canary === 'fake',
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
    const ds = new ApiDataSource(cfg.base, { real: cfg.real, canaryFake: cfg.canaryFake,
                                             fixedRunId: cfg.fixedRunId })
    dsRef.current = ds
    let cancelled = false
    void (async () => {
      try {
        const runId = await ds.createRun()
        if (cancelled) return
        runIdRef.current = runId
        dispatch({ type: 'api_start', runId, replay: !!cfg.fixedRunId })   // fixed run → completed replay
        await ds.subscribe(runId, {
          onEvent: (ev) => dispatch({ type: 'apply_event', ev }),
          onConnection: (c) => dispatch({ type: 'set_connection', connection: c }),
          onCanary: (meta) => dispatch({ type: 'set_canary_meta', meta }),
        })
      } catch {
        dispatch({ type: 'set_connection', connection: 'reconnecting' })
      }
    })()
    return () => { cancelled = true; ds.dispose() }
  }, [])

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
