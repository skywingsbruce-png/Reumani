// Pure mapping from backend RuntimeEvent -> UI state slices. Idempotency (dedupe by
// sequence) is handled by the caller; this function only projects one event.
import type { RuntimeEvent } from './runtimeEvents'
import type { PlanStep, TimelineEvent, TraceEvent, Artifact, RuntimeState,
  TimelineEventType, StepStatus, VerifierStatus, ProvenanceStatus, ArtifactPreviewKind } from '../types'

export type RunStatus = 'running' | 'finished' | 'failed' | 'stopped'

export interface RuntimeSlice {
  taskId: string
  planSteps: PlanStep[]
  timeline: TimelineEvent[]
  trace: TraceEvent[]
  artifacts: Artifact[]
  runtime: RuntimeState
  runStatus: RunStatus
}

const TL_MAP: Record<string, TimelineEventType> = {
  run_created: 'plan_created', plan_ready: 'plan_created', step_started: 'tool_selected',
  attempt_authorized: 'tool_selected', attempt_denied: 'clarification_requested',
  tool_started: 'tool_started', tool_returned: 'observation', observation_recorded: 'observation',
  evidence_accumulated: 'evidence_card', step_satisfied: 'verifier_result',
  step_insufficient: 'verifier_result', step_failed: 'stopped', step_blocked: 'clarification_requested',
  synthesis_started: 'plan_created', synthesis_completed: 'plan_created',
  verification_completed: 'verifier_result', claims_extracted: 'observation',
  claim_graph_completed: 'observation', shadow_completed: 'observation',
  artifact_created: 'artifact_created', run_completed: 'resumed', run_failed: 'stopped',
  run_stopped: 'stopped',
}
const STEP_STATUS: Record<string, StepStatus> = {
  step_satisfied: 'satisfied', step_insufficient: 'insufficient',
  step_failed: 'failed', step_blocked: 'blocked',
}
const PREVIEW_KIND: Record<string, ArtifactPreviewKind> = {
  md: 'markdown', json: 'json', jsonl: 'json', csv: 'csv', png: 'image', pdf: 'markdown',
}

function clock(ts: string): string {
  const t = ts.includes('T') ? ts.split('T')[1] : ts
  return t.slice(0, 5)
}

export function applyRuntimeEvent(s: RuntimeSlice, ev: RuntimeEvent): RuntimeSlice {
  const taskId = s.taskId
  const sp = ev.safe_payload
  let { planSteps, timeline, trace, artifacts, runtime, runStatus } = s

  // timeline entry for every event (best-effort type mapping; real type in title)
  timeline = [...timeline, {
    id: ev.event_id, taskId, type: TL_MAP[ev.event_type] ?? 'observation',
    at: clock(ev.timestamp), title: ev.event_type, detail: ev.summary,
    status: ev.status ?? undefined,
  }]

  switch (ev.event_type) {
    case 'run_created':
      runtime = { phase: 'running', elapsedMs: 0 }; runStatus = 'running'; break
    case 'step_started': {
      const id = `step-${ev.step_id}`
      if (!planSteps.some((p) => p.id === id)) {
        planSteps = [...planSteps, {
          id, taskId, index: ev.step_id ?? planSteps.length + 1,
          objective: String(sp.step_objective ?? ev.summary), status: 'running',
          attempts: 0, callBudget: 0, evidenceCardCount: 0, remainingGaps: [],
        }]
      }
      break
    }
    case 'tool_returned':
      trace = [...trace, {
        eventIndex: trace.length + 1, stage: `step-${ev.step_id}`,
        toolName: String(sp.tool_name ?? ''), status: String(sp.retrieval_status ?? ev.status ?? ''),
        durationMs: Number(sp.duration_ms ?? 0),
        structured: sp.structured ? 'structured' : 'legacy',
        evidenceCount: 0, resultHashShort: String(sp.result_hash || '—'),
      }]
      break
    case 'evidence_accumulated': {
      const count = Number(sp.evidence_count ?? 0)
      planSteps = planSteps.map((p) => p.id === `step-${ev.step_id}`
        ? { ...p, evidenceCardCount: count } : p)
      if (trace.length) {
        trace = trace.map((t, i) => i === trace.length - 1 ? { ...t, evidenceCount: count } : t)
      }
      break
    }
    case 'step_satisfied': case 'step_insufficient': case 'step_failed': case 'step_blocked': {
      const gaps = Array.isArray(sp.remaining_gaps) ? (sp.remaining_gaps as string[]) : []
      planSteps = planSteps.map((p) => p.id === `step-${ev.step_id}`
        ? { ...p, status: STEP_STATUS[ev.event_type], remainingGaps: gaps,
            attempts: Number(sp.attempt_number ?? p.attempts), detail: ev.summary } : p)
      break
    }
    case 'artifact_created': {
      const kind = String(sp.artifact_kind ?? 'json') as Artifact['kind']
      const id = ev.artifact_ids[0] ?? ev.event_id
      if (!artifacts.some((a) => a.id === id)) {
        artifacts = [...artifacts, {
          id, taskId, name: String(sp.artifact_name ?? 'artifact'), kind,
          sizeBytes: Number(sp.size_bytes ?? 0), createdAt: ev.timestamp,
          verifierStatus: (String(sp.verifier_status ?? 'not_run')) as VerifierStatus,
          provenanceStatus: (String(sp.provenance_status ?? 'pending')) as ProvenanceStatus,
          hashShort: String(sp.hash_short ?? ''), previewKind: PREVIEW_KIND[kind] ?? 'json',
          preview: `${ev.summary}\n\n（API 模式：内容由后端产出，已脱敏。）`,
        }]
      }
      break
    }
    case 'run_completed':
      runtime = { ...runtime, phase: 'stopped' }; runStatus = 'finished'; break
    case 'run_failed':
      runtime = { ...runtime, phase: 'stopped' }; runStatus = 'failed'; break
    case 'run_stopped':
      runtime = { ...runtime, phase: 'stopped' }; runStatus = 'stopped'; break
  }
  return { taskId, planSteps, timeline, trace, artifacts, runtime, runStatus }
}
