// Network layer for API mode — the ONLY place fetch/EventSource are used.
// Components never call fetch directly; they go through the store which owns this.
import { parseRuntimeEvent, isTerminal, type RuntimeEvent } from './runtimeEvents'

export type Connection = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ApiHandlers {
  onEvent: (ev: RuntimeEvent) => void
  onConnection: (c: Connection) => void
  onCanary?: (meta: Record<string, unknown>) => void
}

interface EventSourceLike {
  onmessage: ((e: { data: string; lastEventId?: string }) => void) | null
  onerror: ((e: unknown) => void) | null
  onopen: ((e: unknown) => void) | null
  close: () => void
}
type ESFactory = (url: string) => EventSourceLike

export interface ApiOptions {
  fetchImpl?: typeof fetch
  eventSourceFactory?: ESFactory
  stepDelayMs?: number
  real?: boolean            // true → backend runs the REAL deterministic component chain
  canaryFake?: boolean      // true → POST /api/canary-runs (zero-paid fake model canary)
  fixedRunId?: string       // set → subscribe to an existing (e.g. real canary) run, don't create
}

export class ApiDataSource {
  private base: string
  private fetchImpl: typeof fetch
  private esFactory: ESFactory
  private stepDelayMs: number
  private real: boolean
  private canaryFake: boolean
  private fixedRunId?: string
  private es: EventSourceLike | null = null
  private disposed = false

  constructor(base: string, opts: ApiOptions = {}) {
    this.base = base.replace(/\/$/, '')
    this.fetchImpl = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a))
    this.esFactory = opts.eventSourceFactory ?? ((url) => new EventSource(url) as unknown as EventSourceLike)
    this.stepDelayMs = opts.stepDelayMs ?? 300
    this.real = opts.real ?? false
    this.canaryFake = opts.canaryFake ?? false
    this.fixedRunId = opts.fixedRunId
  }

  /** Create a run (or return the fixed existing run) and return its run_id. */
  async createRun(): Promise<string> {
    if (this.fixedRunId) return this.fixedRunId          // subscribe to an existing (real canary) run
    const url = this.canaryFake ? `${this.base}/api/canary-runs` : `${this.base}/api/demo-runs`
    const created = await this.fetchImpl(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ step_delay_ms: this.stepDelayMs, real: this.real }),
    })
    return (await created.json()).run_id as string
  }

  /** Apply the initial snapshot, then stream live events. Dedupe by sequence is the consumer's job. */
  async subscribe(runId: string, h: ApiHandlers): Promise<void> {
    h.onConnection('connecting')
    try {
      const snapRes = await this.fetchImpl(`${this.base}/api/runs/${runId}`)   // snapshot (canary meta)
      const snap = await snapRes.json()
      if (snap && snap.canary && h.onCanary) h.onCanary(snap.canary as Record<string, unknown>)
    } catch { /* meta best-effort */ }
    try {
      const evRes = await this.fetchImpl(`${this.base}/api/runs/${runId}/events`)
      const list = (await evRes.json()).events as unknown[]
      for (const raw of list) {
        const p = parseRuntimeEvent(raw)
        if (p.ok) h.onEvent(p.event)
      }
    } catch { /* snapshot best-effort; SSE will replay */ }
    this.openStream(runId, h)
  }

  private openStream(runId: string, h: ApiHandlers) {
    if (this.disposed) return
    const es = this.esFactory(`${this.base}/api/runs/${runId}/events/stream`)
    this.es = es
    es.onopen = () => h.onConnection('open')
    es.onmessage = (e) => {
      const p = parseRuntimeEvent(JSON.parse(e.data))
      if (!p.ok) return                              // fail-closed: ignore unparseable
      h.onEvent(p.event)
      if (isTerminal(p.event.event_type)) { es.close(); this.es = null; h.onConnection('closed') }
    }
    es.onerror = () => { if (!this.disposed) h.onConnection('reconnecting') }
  }

  async stop(runId: string): Promise<void> {
    await this.fetchImpl(`${this.base}/api/runs/${runId}/stop`, { method: 'POST' })
  }

  dispose() {
    this.disposed = true
    if (this.es) { this.es.close(); this.es = null }
  }
}
