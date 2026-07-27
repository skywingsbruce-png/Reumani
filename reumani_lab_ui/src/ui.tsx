import type { StepStatus, TaskStatus, VerifierStatus } from './types'

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

// Status → {class, glyph, label}. Status is always shown with a glyph + label, not color alone.
const STEP: Record<StepStatus, { c: string; g: string; label: string }> = {
  pending: { c: 'b-idle', g: '○', label: 'Pending' },
  running: { c: 'b-run', g: '▶', label: 'Running' },
  satisfied: { c: 'b-ok', g: '✓', label: 'Satisfied' },
  insufficient: { c: 'b-warn', g: '◐', label: 'Insufficient' },
  failed: { c: 'b-fail', g: '✕', label: 'Failed' },
  blocked: { c: 'b-warn', g: '⏸', label: 'Blocked' },
}
const TASK: Record<TaskStatus, { c: string; g: string; label: string; dot: string }> = {
  awaiting_input: { c: 'b-warn', g: '⏸', label: 'Awaiting input', dot: 'var(--st-warn)' },
  running: { c: 'b-run', g: '▶', label: 'Running', dot: 'var(--st-run)' },
  completed: { c: 'b-ok', g: '✓', label: 'Completed', dot: 'var(--st-ok)' },
  failed: { c: 'b-fail', g: '✕', label: 'Failed / review', dot: 'var(--st-fail)' },
}
const VERIF: Record<VerifierStatus, { c: string; g: string; label: string }> = {
  passed: { c: 'b-ok', g: '✓', label: 'Verifier: passed' },
  not_passed: { c: 'b-fail', g: '✕', label: 'Verifier: not passed' },
  insufficient_for_causal: { c: 'b-warn', g: '◐', label: 'Insufficient for causal' },
  pending: { c: 'b-idle', g: '○', label: 'Verifier: pending' },
  not_run: { c: 'b-idle', g: '–', label: 'Verifier: not run' },
}

export function StepBadge({ status }: { status: StepStatus }) {
  const s = STEP[status] ?? { c: 'b-idle', g: '○', label: String(status) }
  return <span className={`badge ${s.c}`}><span className="g" aria-hidden>{s.g}</span>{s.label}</span>
}
export function TaskBadge({ status }: { status: TaskStatus }) {
  const s = TASK[status] ?? { c: 'b-idle', g: '○', label: String(status), dot: 'var(--st-idle)' }
  return <span className={`badge ${s.c}`}><span className="g" aria-hidden>{s.g}</span>{s.label}</span>
}
export function taskDot(status: TaskStatus) { return TASK[status].dot }
export function VerifierBadge({ status }: { status: VerifierStatus }) {
  const s = VERIF[status] ?? { c: 'b-idle', g: '–', label: `Verifier: ${status}` }
  return <span className={`badge ${s.c}`} title={s.label}><span className="g" aria-hidden>{s.g}</span>{s.label}</span>
}

export const fileGlyph: Record<string, string> = {
  pdf: '▤', csv: '▦', json: '{ }', image: '▣', fasta: '≡', other: '▢',
}
export const artGlyph: Record<string, string> = {
  md: '≡', json: '{ }', pdf: '▤', png: '▣', csv: '▦', jsonl: '↳',
}
export const tlGlyph: Record<string, { g: string; bg: string; fg: string }> = {
  user_message: { g: '💬', bg: 'var(--st-idle-bg)', fg: 'var(--st-idle)' },
  plan_created: { g: '◇', bg: 'var(--st-run-bg)', fg: 'var(--st-run)' },
  tool_selected: { g: '⚙', bg: 'var(--st-idle-bg)', fg: 'var(--st-idle)' },
  tool_started: { g: '▶', bg: 'var(--st-run-bg)', fg: 'var(--st-run)' },
  observation: { g: '◎', bg: 'var(--st-ok-bg)', fg: 'var(--st-ok)' },
  evidence_card: { g: '▤', bg: 'var(--st-ok-bg)', fg: 'var(--st-ok)' },
  clarification_requested: { g: '?', bg: 'var(--st-warn-bg)', fg: 'var(--st-warn)' },
  clarification_answered: { g: '✓', bg: 'var(--st-ok-bg)', fg: 'var(--st-ok)' },
  resumed: { g: '↻', bg: 'var(--st-run-bg)', fg: 'var(--st-run)' },
  verifier_result: { g: '⚖', bg: 'var(--st-warn-bg)', fg: 'var(--st-warn)' },
  artifact_created: { g: '▣', bg: 'var(--st-ok-bg)', fg: 'var(--st-ok)' },
  stopped: { g: '■', bg: 'var(--st-fail-bg)', fg: 'var(--st-fail)' },
}
