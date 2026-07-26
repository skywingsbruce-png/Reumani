// Runtime event contract (TS side) — single source of truth is the shared JSON,
// so the Python and TS event enums cannot drift. See tests/test_runtime_events.py.
import contract from '../contracts/reumani-event-v1.json'

export const EVENT_SCHEMA: string = contract.schema_version
export const EVENT_TYPES: readonly string[] = contract.event_types
export const TERMINAL_EVENT_TYPES: readonly string[] = contract.terminal_event_types
export const REQUIRED_FIELDS: readonly string[] = contract.required
export const SAFE_PAYLOAD_KEYS: readonly string[] = contract.safe_payload_keys

export interface RuntimeEvent {
  schema_version: string
  event_id: string
  run_id: string
  sequence: number
  timestamp: string
  event_type: string
  step_id?: number | null
  status?: string | null
  summary: string
  evidence_ids: string[]
  artifact_ids: string[]
  safe_payload: Record<string, unknown>
  content_hash: string
}

export type ParseResult =
  | { ok: true; event: RuntimeEvent }
  | { ok: false; reason: 'bad_schema_version' | 'unknown_event_type' | 'missing_field' }

// Fail-closed parser: reject unknown schema_version / missing required fields;
// flag unknown event_type as unsupported (never silently coerce).
export function parseRuntimeEvent(raw: unknown): ParseResult {
  if (typeof raw !== 'object' || raw === null) return { ok: false, reason: 'missing_field' }
  const o = raw as Record<string, unknown>
  for (const f of REQUIRED_FIELDS) {
    if (o[f] === undefined || o[f] === null) return { ok: false, reason: 'missing_field' }
  }
  if (o.schema_version !== EVENT_SCHEMA) return { ok: false, reason: 'bad_schema_version' }
  if (!EVENT_TYPES.includes(String(o.event_type))) return { ok: false, reason: 'unknown_event_type' }
  const ev: RuntimeEvent = {
    schema_version: String(o.schema_version),
    event_id: String(o.event_id),
    run_id: String(o.run_id),
    sequence: Number(o.sequence),
    timestamp: String(o.timestamp),
    event_type: String(o.event_type),
    step_id: (o.step_id as number | null | undefined) ?? null,
    status: (o.status as string | null | undefined) ?? null,
    summary: String(o.summary ?? ''),
    evidence_ids: Array.isArray(o.evidence_ids) ? (o.evidence_ids as string[]) : [],
    artifact_ids: Array.isArray(o.artifact_ids) ? (o.artifact_ids as string[]) : [],
    safe_payload: (o.safe_payload as Record<string, unknown>) ?? {},
    content_hash: String(o.content_hash),
  }
  return { ok: true, event: ev }
}

export function isTerminal(eventType: string): boolean {
  return TERMINAL_EVENT_TYPES.includes(eventType)
}
